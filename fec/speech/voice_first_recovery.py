"""Voice-first recovery over complete source recordings.

This lane repairs the ordering defect in the original source-media workflow:
the entire secured recording is windowed and compared against every vaulted
speaker before a member-labelled candidate is cut.  Whisper timestamps are
used only after identity, to attach words and descriptive topic context.

Production promotion currently uses the established three-by-eight-second
zero-observed-false-accept contract.  One- and two-window protocols are also
calibrated and serialized here, but remain diagnostic until their distinct
contracts are supported end-to-end by the final consumer.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import itertools
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Sequence
import wave

import numpy as np

from fec.members.b5_audio_embed import get_backend
from fec.speech import source_media_recovery as SMR
from fec.speech import speaker_anchor_recovery as SAR


ROOT = SMR.ROOT
DATA = ROOT / "data" / "speech"
DEFAULT_ANCHOR_RECEIPT = DATA / "speaker_anchor_vault_expanded_v2.receipt.json"
DEFAULT_CALIBRATION = DATA / "speaker_anchor_calibration_expanded_v2.json"
DEFAULT_MULTI_CALIBRATION = DATA / "speaker_anchor_calibration_multilength_v1.json"
DEFAULT_ALL_VERIFIED_MANIFEST = DATA / "speaker_anchor_vault_all_verified_v3.jsonl"
DEFAULT_ALL_VERIFIED_RECEIPT = DATA / "speaker_anchor_vault_all_verified_v3.receipt.json"
DEFAULT_ALL_VERIFIED_VECTORS = Path(
    r"C:\melange-data\speaker_anchor_vault_all_verified_v3\ecapa_anchor_vectors_v3.npz"
)
DEFAULT_INVENTORY = DATA / "source_media_inventory_cumulative_expanded_v3_snapshot.jsonl"
DEFAULT_CNN_ROUTES = DATA / "source_media_local_reuse_cnn_v1.jsonl"
DEFAULT_FOX_ROOT = Path(r"C:\melange-data\source_audio_fox_targeted")
DEFAULT_GAP = DATA / "member_speech_profile_gap_v1.jsonl"
DEFAULT_OUTPUT_ROOT = Path(r"C:\melange-data\source_media_voice_first_v1")
DEFAULT_SOURCE_MANIFEST = DATA / "source_media_voice_first_sources_v1.jsonl"
DEFAULT_WINDOW_MANIFEST = DATA / "source_media_voice_first_windows_v1.jsonl"
DEFAULT_SCAN_RECEIPT = DATA / "source_media_voice_first_scan_v1.receipt.json"

MULTI_CALIBRATION_SCHEMA = "speech-speaker-anchor-multilength-calibration-v1"
SOURCE_SCHEMA = "speech-source-voice-first-input-v1"
WINDOW_SCHEMA = "speech-source-voice-first-window-v1"
SCAN_RECEIPT_SCHEMA = "speech-source-voice-first-scan-receipt-v1"
WINDOW_SEC = 8
SAMPLE_RATE = 16000
WINDOW_FRAMES = WINDOW_SEC * SAMPLE_RATE
GRID_OFFSETS = (0, 4 * SAMPLE_RATE)


class VoiceFirstRefused(RuntimeError):
    """Raised when a voice-first evidence binding is incomplete."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VoiceFirstRefused(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise VoiceFirstRefused(f"JSON artifact root is not an object: {path}")
    return value


def protocol_contract(window_count: int) -> dict[str, Any]:
    if window_count not in {1, 2, 3}:
        raise VoiceFirstRefused("protocol window count must be one, two, or three")
    return {
        "schema": "speech-speaker-identity-scoring-contract-multilength-v1",
        "candidate_window_sec": WINDOW_SEC,
        "sequence_windows": window_count,
        "sequence_choice": (
            "every single held-out window" if window_count == 1 else
            "every unordered pair from three held-out windows" if window_count == 2 else
            "the complete three-window held-out sequence"
        ),
        "target_aggregation": "maximum cosine across every anchor for the candidate speaker",
        "competitor_aggregation": "maximum cosine for every other vaulted speaker",
        "sequence_aggregation": "every window must have the same unique winner and pass independently",
        "all_vaulted_speakers_compared": True,
        "held_out_source_event": True,
        "program_title_date_airtime_used": False,
        "production_promotion": window_count == 3,
    }


def _thresholds(
    genuine: list[tuple[float, float, bool]],
    false_trials: list[tuple[float, float, bool]],
) -> dict[str, Any]:
    if not genuine or not false_trials:
        raise VoiceFirstRefused("calibration lacks genuine or wrong-claim trials")
    false_unanimous = [trial for trial in false_trials if trial[2]]
    maximum_false_margin = max((trial[1] for trial in false_unanimous), default=0.0)
    margin_floor = math.ceil((max(0.0, maximum_false_margin) + 1e-6) * 1e9) / 1e9
    passing_genuine = [
        (score, margin) for score, margin, unanimous in genuine
        if unanimous and margin >= margin_floor
    ]
    score_floor = math.floor(
        (min((score for score, _ in passing_genuine), default=1.0) - 1e-9) * 1e9
    ) / 1e9
    false_accepts = sum(
        unanimous and score >= score_floor and margin >= margin_floor
        for score, margin, unanimous in false_trials
    )
    genuine_accepts = sum(
        unanimous and score >= score_floor and margin >= margin_floor
        for score, margin, unanimous in genuine
    )
    return {
        "status": (
            "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
            if false_accepts == 0 and genuine_accepts > 0
            else "CALIBRATION_STOP_BAR_MISS"
        ),
        "minimum_target_best_score": score_floor,
        "minimum_best_anchor_margin": margin_floor,
        "genuine_sequences": len(genuine),
        "genuine_accepts": genuine_accepts,
        "genuine_accept_rate": genuine_accepts / len(genuine),
        "wrong_claim_sequences": len(false_trials),
        "false_unanimous_sequences": len(false_unanimous),
        "false_accepts": false_accepts,
        "empirical_precision": 1.0 if false_accepts == 0 else None,
    }


def _claim_thresholds(
    genuine: list[tuple[float, float, bool]],
    false_trials: list[tuple[float, float, bool]],
) -> dict[str, Any]:
    """Derive one claim's zero-FAR boundary without a global worst-pair tax."""
    if not false_trials:
        raise VoiceFirstRefused("per-claim calibration lacks wrong-claim trials")
    false_unanimous = [trial for trial in false_trials if trial[2]]
    maximum_false_margin = max(
        (trial[1] for trial in false_unanimous), default=0.0
    )
    margin_floor = math.ceil(
        (max(0.0, maximum_false_margin) + 1e-6) * 1e9
    ) / 1e9
    passing_genuine = [
        (score, margin) for score, margin, unanimous in genuine
        if unanimous and margin >= margin_floor
    ]
    score_floor = math.floor(
        (min((score for score, _ in passing_genuine), default=0.0) - 1e-9) * 1e9
    ) / 1e9
    false_accepts = sum(
        unanimous and score >= score_floor and margin >= margin_floor
        for score, margin, unanimous in false_trials
    )
    genuine_accepts = sum(
        unanimous and score >= score_floor and margin >= margin_floor
        for score, margin, unanimous in genuine
    )
    return {
        "minimum_target_best_score": score_floor,
        "minimum_best_anchor_margin": margin_floor,
        "genuine_sequences": len(genuine),
        "genuine_accepts": genuine_accepts,
        "held_out_genuine_available": bool(genuine),
        "wrong_claim_sequences": len(false_trials),
        "false_unanimous_sequences": len(false_unanimous),
        "false_accepts": false_accepts,
    }


def calibrate_multilength(
    vault: dict[str, np.ndarray], eligible_targets: set[str]
) -> dict[str, Any]:
    vectors = np.asarray(vault["vectors"], dtype=np.float64)
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    members = np.asarray(vault["members"]).astype(str)
    events = np.asarray(vault["event_ids"]).astype(str)
    calibration_vectors = np.asarray(vault["calibration_vectors"], dtype=np.float64)
    calibration_vectors /= np.maximum(
        np.linalg.norm(calibration_vectors, axis=1, keepdims=True), 1e-12
    )
    calibration_members = np.asarray(vault["calibration_members"]).astype(str)
    calibration_events = np.asarray(vault["calibration_event_ids"]).astype(str)
    calibration_indexes = np.asarray(vault["calibration_window_indexes"], dtype=int)
    member_names = sorted(set(members.tolist()))
    eligible = [member for member in member_names if member in eligible_targets]
    if len(member_names) < 2 or not eligible:
        raise VoiceFirstRefused("calibration vault lacks speakers or eligible targets")
    trials: dict[int, dict[str, list[tuple[float, float, bool]]]] = {
        count: {"genuine": [], "false": []} for count in (1, 2, 3)
    }
    claim_trials = {
        count: {
            member: {"genuine": [], "false": []} for member in eligible
        } for count in (1, 2, 3)
    }
    evaluated_events = 0
    skipped_events = 0
    for held_out_event in sorted(set(calibration_events.tolist())):
        window_rows = np.flatnonzero(calibration_events == held_out_event)
        if (
            window_rows.size != 3
            or sorted(calibration_indexes[window_rows].tolist()) != [0, 1, 2]
        ):
            raise VoiceFirstRefused("calibration event windows are incomplete")
        order = np.argsort(calibration_indexes[window_rows])
        window_rows = window_rows[order]
        true_names = set(calibration_members[window_rows].tolist())
        if len(true_names) != 1:
            raise VoiceFirstRefused("calibration event has multiple speakers")
        true_member = next(iter(true_names))
        training_rows = np.flatnonzero(events != held_out_event)
        training_members = members[training_rows]
        reference_count = int(np.sum(training_members == true_member))
        if reference_count:
            evaluated_events += 1
        else:
            skipped_events += 1
        similarity = calibration_vectors[window_rows] @ vectors[training_rows].T
        best = np.empty((3, len(member_names)), dtype=np.float64)
        for member_index, member in enumerate(member_names):
            indexes = np.flatnonzero(training_members == member)
            best[:, member_index] = (
                np.max(similarity[:, indexes], axis=1) if indexes.size else -np.inf
            )
        eligible_indexes = np.asarray([member_names.index(name) for name in eligible], dtype=int)
        ranking = np.argsort(best, axis=1)
        top_indexes = ranking[:, -1]
        top_scores = best[np.arange(3), top_indexes]
        second_scores = best[np.arange(3), ranking[:, -2]]
        for count in (1, 2, 3):
            for combination in itertools.combinations(range(3), count):
                rows = np.asarray(combination, dtype=int)
                target = best[rows][:, eligible_indexes]
                competitor = np.where(
                    top_indexes[rows, None] == eligible_indexes[None, :],
                    second_scores[rows, None], top_scores[rows, None],
                )
                scores = np.min(target, axis=0)
                margins = np.min(target - competitor, axis=0)
                unanimous = np.all(target > competitor, axis=0)
                for index, claimed in enumerate(eligible):
                    trial = (float(scores[index]), float(margins[index]), bool(unanimous[index]))
                    if claimed == true_member:
                        # When the held-out event is the speaker's only event,
                        # there is no remaining production reference for a
                        # genuine trial.  Omit that self-claim exactly as the
                        # production calibrator does; it is not an impostor.
                        if reference_count:
                            trials[count]["genuine"].append(trial)
                            claim_trials[count][claimed]["genuine"].append(trial)
                    else:
                        trials[count]["false"].append(trial)
                        claim_trials[count][claimed]["false"].append(trial)
    protocols: dict[str, Any] = {}
    for count in (1, 2, 3):
        contract = protocol_contract(count)
        result = _thresholds(trials[count]["genuine"], trials[count]["false"])
        per_claim = {
            member: _claim_thresholds(
                claim_trials[count][member]["genuine"],
                claim_trials[count][member]["false"],
            ) for member in eligible
        }
        per_claim_false_accepts = sum(
            int(row["false_accepts"]) for row in per_claim.values()
        )
        per_claim_genuine_accepts = sum(
            int(row["genuine_accepts"]) for row in per_claim.values()
        )
        protocols[f"{count}x8"] = {
            **result,
            "window_count": count,
            "window_sec": WINDOW_SEC,
            "scoring_contract": contract,
            "scoring_contract_sha256": SMR.sha256_json(contract),
            "all_vaulted_speakers_compared": True,
            "program_title_date_airtime_used": False,
            "per_claim_thresholds": per_claim,
            "per_claim_false_accepts": per_claim_false_accepts,
            "per_claim_genuine_accepts": per_claim_genuine_accepts,
            "per_claim_thresholds_status": (
                "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
                if per_claim_false_accepts == 0 and per_claim_genuine_accepts > 0
                else "CALIBRATION_STOP_BAR_MISS"
            ),
        }
    return {
        "schema": MULTI_CALIBRATION_SCHEMA,
        "status": (
            "PASS_ZERO_FALSE_ACCEPT_ALL_PROTOCOLS"
            if all(row["status"] == "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
                   and row["per_claim_thresholds_status"]
                   == "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
                   for row in protocols.values())
            else "CALIBRATION_STOP_BAR_MISS"
        ),
        "operational_confidence_label": "100_PERCENT_STRUCTURAL_CONSENSUS",
        "probability_claim": False,
        "vaulted_speakers": len(member_names),
        "eligible_target_speakers": len(eligible),
        "held_out_events": len(set(calibration_events.tolist())),
        "held_out_events_evaluated": evaluated_events,
        "events_skipped_no_remaining_true_reference": skipped_events,
        "protocols": protocols,
    }


def run_calibrate(args: argparse.Namespace) -> int:
    receipt_path = Path(args.anchor_receipt)
    receipt = _read_json(receipt_path)
    vault_path = Path(str(receipt.get("combined_vectors") or ""))
    if (
        receipt.get("status") != "PASS"
        or not vault_path.is_file()
        or receipt.get("combined_vectors_sha256") != SMR.sha256_file(vault_path)
    ):
        raise VoiceFirstRefused("expanded anchor vault receipt is stale")
    vault = SAR.load_vault(vault_path)
    calibration = calibrate_multilength(
        vault, set(receipt.get("eligible_targets") or [])
    )
    production = _read_json(Path(args.production_calibration))
    control = calibration["protocols"]["3x8"]
    if (
        production.get("status") != "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
        or int(production.get("false_accepts", -1)) != 0
        or float(production.get("minimum_target_best_score"))
        != float(control["minimum_target_best_score"])
        or float(production.get("minimum_best_anchor_margin"))
        != float(control["minimum_best_anchor_margin"])
        or int(production.get("wrong_claim_sequences", -1))
        != int(control["wrong_claim_sequences"])
    ):
        raise VoiceFirstRefused("three-window control does not reproduce production calibration")
    calibration.update({
        "generated_at": SMR.utc_now(),
        "anchor_receipt": str(receipt_path.resolve()),
        "anchor_receipt_sha256": SMR.sha256_file(receipt_path),
        "anchor_manifest_sha256": receipt.get("manifest_sha256"),
        "anchor_vectors_sha256": receipt.get("combined_vectors_sha256"),
        "calibration_window_manifest_sha256": receipt.get(
            "calibration_window_manifest_sha256"
        ),
        "model_manifest_sha256": receipt.get("model_manifest_sha256"),
        "production_three_window_calibration": str(
            Path(args.production_calibration).resolve()
        ),
        "production_three_window_calibration_sha256": SMR.sha256_file(
            Path(args.production_calibration)
        ),
        "three_window_control_exact": True,
        "shorter_protocols_promotable_in_current_consumer": False,
    })
    SMR.write_json_atomic(Path(args.multi_calibration), calibration)
    print(json.dumps({
        "status": calibration["status"],
        "artifact": str(Path(args.multi_calibration).resolve()),
        "sha256": SMR.sha256_file(Path(args.multi_calibration)),
        "protocols": {
            key: {
                "wrong_claim_sequences": value["wrong_claim_sequences"],
                "false_accepts": value["false_accepts"],
                "genuine_accepts": value["genuine_accepts"],
                "minimum_target_best_score": value["minimum_target_best_score"],
                "minimum_best_anchor_margin": value["minimum_best_anchor_margin"],
            } for key, value in calibration["protocols"].items()
        },
    }, sort_keys=True))
    return 0 if calibration["status"] == "PASS_ZERO_FALSE_ACCEPT_ALL_PROTOCOLS" else 4


def physical_member_coverage(source_rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Derive the locked target prerequisite from atomic physical anchor rows."""
    coverage = {}
    for member in sorted({row["member"] for row in source_rows}):
        member_rows = [row for row in source_rows if row["member"] == member]
        duration = sum(float(row.get("duration_sec") or 0.0) for row in member_rows)
        events = len({row["event_id"] for row in member_rows})
        eligible = (
            len(member_rows) >= SAR.MIN_TARGET_ANCHORS
            and events >= SAR.MIN_TARGET_EVENTS
            and duration >= SAR.MIN_TARGET_SECONDS
        )
        coverage[member] = {
            "anchors": len(member_rows),
            "events": events,
            "duration_sec": round(duration, 6),
            "target_eligible": eligible,
            "eligibility_basis": "physical-anchor-count+distinct-events+recorded-duration",
        }
    return coverage


def run_expand_vault(args: argparse.Namespace) -> int:
    """Recompute target eligibility from physical anchors without changing vectors.

    Accepted voices that do not meet the three-anchor, three-event, 180-second
    prerequisite remain valuable *competitors* in the exhaustive vault.  They
    are not valid target identities.  Eligibility is derived from the manifest
    on every run and can never be granted merely because one accepted reference
    exists.
    """
    base_receipt_path = Path(args.anchor_receipt)
    base_receipt = _read_json(base_receipt_path)
    base_vault_path = Path(str(base_receipt.get("combined_vectors") or ""))
    base_manifest_path = Path(str(base_receipt.get("manifest") or ""))
    if (
        base_receipt.get("status") != "PASS"
        or not base_vault_path.is_file()
        or base_receipt.get("combined_vectors_sha256") != SMR.sha256_file(base_vault_path)
        or not base_manifest_path.is_file()
        or base_receipt.get("manifest_sha256") != SMR.sha256_file(base_manifest_path)
    ):
        raise VoiceFirstRefused("base accepted-identity vault is stale")
    vault = SAR.load_vault(base_vault_path)
    source_rows = SMR.read_jsonl(base_manifest_path, required=True)
    member_coverage = physical_member_coverage(source_rows)
    rows = []
    for source_row in source_rows:
        row = dict(source_row)
        row["target_eligible"] = member_coverage[row["member"]]["target_eligible"]
        row["anchor_row_sha256"] = SAR._row_hash(row, "anchor_row_sha256")
        rows.append(row)
    members = sorted(set(vault["members"].astype(str).tolist()))
    coverage = {member: member_coverage[member] for member in members}
    eligible_members = sorted(
        member for member in members if coverage[member]["target_eligible"]
    )
    manifest_path = Path(args.all_verified_manifest)
    SMR.write_jsonl_atomic(manifest_path, rows)
    receipt = {
        **base_receipt,
        "status": "PASS",
        "generated_at": SMR.utc_now(),
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": SMR.sha256_file(manifest_path),
        "accepted_anchors": len(rows),
        "anchors_total": len(rows),
        "anchors_embedded": len(rows),
        "vaulted_speakers": len(members),
        "eligible_target_speakers": len(eligible_members),
        "eligible_targets": eligible_members,
        "coverage": coverage,
        "duration_hours": base_receipt.get("duration_hours"),
        "combined_vectors": str(base_vault_path.resolve()),
        "combined_vectors_sha256": base_receipt.get("combined_vectors_sha256"),
        "primary_event_ids_unique": base_receipt.get("primary_event_ids_unique") is True,
        "same_event_anchor_windows_excluded_together": base_receipt.get(
            "same_event_anchor_windows_excluded_together", True
        ),
        "identity_eligibility_contract": (
            "accepted voices remain exhaustive competitors; target eligibility requires "
            "three physical anchors, three distinct events, and 180 recorded seconds"
        ),
        "base_anchor_receipt": str(base_receipt_path.resolve()),
        "base_anchor_receipt_sha256": SMR.sha256_file(base_receipt_path),
        "base_anchor_vectors_sha256": base_receipt.get("combined_vectors_sha256"),
        "source_audio_cleanup_authorized": False,
    }
    SMR.write_json_atomic(Path(args.all_verified_receipt), receipt)
    print(json.dumps({
        "status": "PASS", "anchors": len(rows), "vaulted_speakers": len(members),
        "eligible_target_speakers": len(eligible_members),
        "manifest_sha256": receipt["manifest_sha256"],
        "vectors_sha256": receipt["combined_vectors_sha256"],
    }, sort_keys=True))
    return 0


def _inventory_indexes(rows: Sequence[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_package = {str(row.get("package") or ""): row for row in rows}
    by_package_id = {str(row.get("package_id") or ""): row for row in rows}
    return by_package, by_package_id


def collect_sources(
    inventory_rows: Sequence[dict[str, Any]], cnn_routes: Path, fox_root: Path
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_package, by_package_id = _inventory_indexes(inventory_rows)
    sources: dict[str, dict[str, Any]] = {}
    skips = {"fox_skipped_not_in_inventory": 0, "fox_skipped_missing_asr": 0}

    def add(
        source_receipt_path: Path, transcript_receipt_path: Path,
        transcript_path: Path, inventory_row: dict[str, Any], source_kind: str,
    ) -> None:
        source_receipt = _read_json(source_receipt_path)
        transcript_receipt = _read_json(transcript_receipt_path)
        wav = Path(str(source_receipt.get("wav") or ""))
        source_sha = str(
            source_receipt.get("wav_sha256")
            or source_receipt.get("source_audio_sha256") or ""
        )
        reference_url = str(source_receipt.get("reference_url") or "")
        if (
            source_receipt.get("schema") != "speech-source-media-acquisition-v1"
            or not wav.is_absolute() or not wav.is_file()
            or len(source_sha) != 64 or SMR.sha256_file(wav) != source_sha
            or transcript_receipt.get("schema") != SMR.ASR_RECEIPT_SCHEMA
            or transcript_receipt.get("status") != "PASS"
            or transcript_receipt.get("speaker_identity_authority") is not False
            or transcript_receipt.get("source_audio_sha256") != source_sha
            or transcript_receipt.get("transcript_sha256") != SMR.sha256_file(transcript_path)
            or str(transcript_receipt.get("reference_url") or "") != reference_url
            or not reference_url.startswith(("https://", "http://"))
        ):
            raise VoiceFirstRefused(f"source/transcript chain is stale: {wav}")
        duration, frames = SAR.wav_contract(wav)
        row = {
            "schema": SOURCE_SCHEMA,
            "physical_source_id": str(source_receipt.get("media_id") or source_sha[:24]),
            "source_kind": source_kind,
            "source_family": str(inventory_row.get("source_family") or ""),
            "show": str(inventory_row.get("show") or ""),
            "date": str(inventory_row.get("date") or ""),
            "package": str(inventory_row.get("package") or ""),
            "package_id": str(inventory_row.get("package_id") or ""),
            "inventory_row_sha256": SMR.sha256_json(inventory_row),
            "provider": str(source_receipt.get("provider") or ""),
            "media_id": str(source_receipt.get("media_id") or ""),
            "reference_url": reference_url,
            "source_offset_sec": float(source_receipt.get("source_offset_sec") or 0.0),
            "source_wav": str(wav.resolve()),
            "source_audio_sha256": source_sha,
            "source_frames": frames,
            "duration_sec": round(duration, 6),
            "source_receipt": str(source_receipt_path.resolve()),
            "source_receipt_sha256": SMR.sha256_file(source_receipt_path),
            "asr_transcript": str(transcript_path.resolve()),
            "asr_transcript_sha256": SMR.sha256_file(transcript_path),
            "asr_transcript_receipt": str(transcript_receipt_path.resolve()),
            "asr_transcript_receipt_sha256": SMR.sha256_file(transcript_receipt_path),
            "program_title_date_airtime_identity_authority": False,
        }
        row["source_row_sha256"] = SAR._row_hash(row, "source_row_sha256")
        prior = sources.get(source_sha)
        if prior and prior != row:
            # Multiple package routes may point to one physical source.  Keep
            # one deterministic inventory route; identity never uses it.
            if row["package"] < prior["package"]:
                sources[source_sha] = row
        else:
            sources[source_sha] = row

    for route in SMR.read_jsonl(cnn_routes, required=True):
        asr = route.get("asr") or {}
        package_routes = sorted(
            route.get("package_routes") or [], key=lambda value: str(value.get("package") or "")
        )
        inventory_row = next(
            (by_package.get(str(value.get("package") or "")) for value in package_routes
             if by_package.get(str(value.get("package") or ""))), None,
        )
        if inventory_row is None:
            raise VoiceFirstRefused("CNN route is absent from cumulative inventory")
        source_receipt_path = Path(str(asr.get("receipt") or "")).parent / "source.json"
        add(
            source_receipt_path, Path(str(asr.get("receipt") or "")),
            Path(str(asr.get("transcript") or "")), inventory_row,
            str(route.get("source_kind") or "cnn-podcast"),
        )

    for source_receipt_path in sorted(fox_root.rglob("source.json")):
        source_receipt = _read_json(source_receipt_path)
        inventory_row = by_package_id.get(str(source_receipt.get("package_id") or ""))
        if inventory_row is None:
            package = str(source_receipt.get("transcript_package_url") or "")
            inventory_row = by_package.get(package)
        if inventory_row is None:
            skips["fox_skipped_not_in_inventory"] += 1
            continue
        transcript_receipt = source_receipt_path.with_name("transcript.receipt.json")
        transcript = source_receipt_path.with_name("transcript.words.jsonl")
        if transcript_receipt.is_file() and transcript.is_file():
            add(source_receipt_path, transcript_receipt, transcript, inventory_row, "fox-broadcast")
        else:
            skips["fox_skipped_missing_asr"] += 1
    ordered = sorted(sources.values(), key=lambda row: (row["source_family"], row["source_audio_sha256"]))
    return ordered, skips


_WORKER_BACKEND: Any = None


def _worker_init(model_dir: str, model_manifest: str) -> None:
    global _WORKER_BACKEND
    _WORKER_BACKEND = get_backend("ecapa", model_dir, model_manifest)


def all_grid_windows(total_frames: int) -> list[tuple[int, int]]:
    values: list[tuple[int, int]] = []
    for grid in GRID_OFFSETS:
        values.extend(
            (grid, start)
            for start in range(grid, total_frames - WINDOW_FRAMES + 1, WINDOW_FRAMES)
        )
    return values


def seeded_grid_windows(total_frames: int, stride_sec: int = 64) -> list[tuple[int, int]]:
    """Uniformly sample both grids across the full recording for discovery."""
    stride = max(WINDOW_FRAMES, int(stride_sec) * SAMPLE_RATE)
    values: list[tuple[int, int]] = []
    for grid in GRID_OFFSETS:
        values.extend(
            (grid, start)
            for start in range(grid, total_frames - WINDOW_FRAMES + 1, stride)
        )
    return values


def dense_windows_around_seeds(
    total_frames: int,
    seeds: Sequence[tuple[int, int]],
    radius_sec: int = 96,
) -> list[tuple[int, int]]:
    """Return exact adjacent grid windows around accepted discovery seeds."""
    radius = max(WINDOW_FRAMES, int(radius_sec) * SAMPLE_RATE)
    values: set[tuple[int, int]] = set()
    for grid, seed_start in seeds:
        first_index = max(0, math.floor((seed_start - radius - grid) / WINDOW_FRAMES))
        last_index = math.ceil((seed_start + radius - grid) / WINDOW_FRAMES)
        for index in range(first_index, last_index + 1):
            start = grid + index * WINDOW_FRAMES
            if start >= 0 and start + WINDOW_FRAMES <= total_frames:
                values.add((grid, start))
    return sorted(values)


def _save_npz_atomic(path: Path, **arrays: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp = Path(raw)
    try:
        with temp.open("wb") as handle:
            np.savez_compressed(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _embed_source(job: dict[str, Any]) -> dict[str, Any]:
    import soundfile as sf
    import torch
    global _WORKER_BACKEND
    if _WORKER_BACKEND is None:
        raise VoiceFirstRefused("embedding worker backend is not initialized")
    source = job["source"]
    output = Path(job["output"])
    audio, rate = sf.read(source["source_wav"], dtype="float32", always_2d=False)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    audio = audio.reshape(-1)
    if rate != SAMPLE_RATE or audio.size != int(source["source_frames"]):
        raise VoiceFirstRefused("source PCM changed before voice-first embedding")
    windows = [tuple(map(int, value)) for value in job.get("windows") or all_grid_windows(audio.size)]
    vectors: list[np.ndarray] = []
    rms = []
    batch_size = max(1, int(job.get("batch_size") or 8))
    model = _WORKER_BACKEND._load()
    torch.set_num_threads(max(1, int(job.get("torch_threads") or 1)))
    for batch_start in range(0, len(windows), batch_size):
        batch_windows = windows[batch_start:batch_start + batch_size]
        signals = np.stack([
            audio[start:start + WINDOW_FRAMES] for _grid, start in batch_windows
        ]).astype(np.float32, copy=False)
        with torch.no_grad():
            encoded = model.encode_batch(torch.from_numpy(signals)).cpu().numpy()
        encoded = encoded.reshape(encoded.shape[0], -1).astype(np.float64)
        encoded /= np.maximum(np.linalg.norm(encoded, axis=1, keepdims=True), 1e-12)
        vectors.extend(encoded.astype(np.float32))
        rms.extend(
            float(np.sqrt(np.mean(np.square(signal, dtype=np.float64))))
            for signal in signals
        )
    _save_npz_atomic(
        output,
        grid_offset_frames=np.asarray([grid for grid, _ in windows], dtype=np.int32),
        source_start_frame=np.asarray([start for _, start in windows], dtype=np.int64),
        vectors=np.asarray(vectors, dtype=np.float32),
        rms=np.asarray(rms, dtype=np.float32),
    )
    return {
        "source_audio_sha256": source["source_audio_sha256"],
        "embedding_path": str(output.resolve()),
        "embedding_sha256": SMR.sha256_file(output),
        "windows": len(windows),
    }


def _load_words(path: Path) -> list[dict[str, Any]]:
    rows = SMR.read_jsonl(path, required=True)
    return sorted(rows, key=lambda row: (float(row.get("start_sec") or 0), int(row.get("index") or 0)))


def words_for_interval(
    words: Sequence[dict[str, Any]], start_frame: int, frame_count: int
) -> list[dict[str, Any]]:
    start = start_frame / SAMPLE_RATE
    end = (start_frame + frame_count) / SAMPLE_RATE
    return [
        row for row in words
        if float(row.get("end_sec") or 0) > start
        and float(row.get("start_sec") or 0) < end
    ]


TOPIC_LEXICON = {
    "economy": {"economy", "inflation", "jobs", "tax", "wages", "business", "debt"},
    "healthcare": {"health", "healthcare", "medicare", "medicaid", "covid", "hospital"},
    "immigration": {"border", "immigration", "immigrant", "migrant", "asylum"},
    "elections_democracy": {"election", "vote", "voting", "ballot", "democracy", "campaign"},
    "justice_public_safety": {"court", "crime", "police", "justice", "gun", "violence"},
    "foreign_policy_security": {"war", "ukraine", "israel", "china", "russia", "military", "security"},
    "environment_energy": {"climate", "energy", "oil", "gas", "environment", "emissions"},
    "education": {"school", "education", "student", "college", "teacher"},
    "government_congress": {"congress", "senate", "house", "government", "bill", "budget"},
    "social_policy": {"abortion", "rights", "family", "women", "marriage", "religion"},
}


def classify_subject(text: str) -> str:
    tokens = SMR.normalize_tokens(text)
    counts = {
        topic: sum(token in vocabulary for token in tokens)
        for topic, vocabulary in TOPIC_LEXICON.items()
    }
    best = max(counts.items(), key=lambda item: (item[1], item[0]))
    return best[0] if best[1] else "general_public_affairs"


def passing_runs(rows: Sequence[dict[str, Any]], minimum_windows: int = 3) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        grouped[(str(row["source_audio_sha256"]), int(row["grid_offset_frames"]))].append(row)
    runs: list[dict[str, Any]] = []
    for (_source_sha, _grid), values in grouped.items():
        values.sort(key=lambda row: int(row["source_start_frame"]))
        start = 0
        while start < len(values):
            first = values[start]
            if not first.get("passes_3x_window"):
                start += 1
                continue
            end = start + 1
            while (
                end < len(values)
                and values[end].get("passes_3x_window")
                and values[end]["winner"] == first["winner"]
                and int(values[end]["source_start_frame"])
                == int(values[end - 1]["source_start_frame"]) + WINDOW_FRAMES
            ):
                end += 1
            for chunk_start in range(start, end - minimum_windows + 1, minimum_windows):
                chunk = values[chunk_start:chunk_start + minimum_windows]
                if len(chunk) == minimum_windows:
                    runs.append({
                        "source_audio_sha256": first["source_audio_sha256"],
                        "source_start_frame": int(chunk[0]["source_start_frame"]),
                        "source_frame_count": minimum_windows * WINDOW_FRAMES,
                        "member": first["winner"],
                        "window_count": minimum_windows,
                        "minimum_target_best_score": min(float(row["winner_best"]) for row in chunk),
                        "minimum_best_anchor_margin": min(float(row["best_anchor_margin"]) for row in chunk),
                        "grid_offset_frames": int(first["grid_offset_frames"]),
                        "window_row_sha256s": [row["window_row_sha256"] for row in chunk],
                    })
            start = end
    return runs


def select_disjoint_profile_units(
    runs: Sequence[dict[str, Any]], needed_by_member: dict[str, int]
) -> list[dict[str, Any]]:
    by_member: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for run in runs:
        if needed_by_member.get(str(run["member"]), 0) > 0:
            by_member[str(run["member"])].append(run)
    selected: list[dict[str, Any]] = []
    occupied: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    for member, values in sorted(by_member.items()):
        values.sort(key=lambda row: (
            -float(row["minimum_best_anchor_margin"]),
            str(row["source_audio_sha256"]), int(row["source_start_frame"]),
        ))
        chosen: list[dict[str, Any]] = []
        used_sources: set[str] = set()
        for distinct_only in (True, False):
            for row in values:
                if len(chosen) >= needed_by_member[member] or row in chosen:
                    continue
                source_sha = str(row["source_audio_sha256"])
                if distinct_only and source_sha in used_sources:
                    continue
                start = int(row["source_start_frame"])
                count = int(row["source_frame_count"])
                if any(SAR.source_frame_intervals_overlap(start, count, old_start, old_count)
                       for old_start, old_count in occupied[source_sha]):
                    continue
                chosen.append(row)
                selected.append(row)
                used_sources.add(source_sha)
                occupied[source_sha].append((start, count))
    return selected


def _write_pcm_slice(source: Path, target: Path, start: int, count: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temp = Path(raw)
    try:
        with wave.open(str(source), "rb") as source_handle:
            if (source_handle.getnchannels(), source_handle.getsampwidth(), source_handle.getframerate()) != (1, 2, SAMPLE_RATE):
                raise VoiceFirstRefused("source WAV violates mono 16-kHz PCM16 contract")
            source_handle.setpos(start)
            payload = source_handle.readframes(count)
        if len(payload) != count * 2:
            raise VoiceFirstRefused("source PCM slice is incomplete")
        with wave.open(str(temp), "wb") as target_handle:
            target_handle.setnchannels(1)
            target_handle.setsampwidth(2)
            target_handle.setframerate(SAMPLE_RATE)
            target_handle.writeframes(payload)
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()


PER_CLAIM_PASS = "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"


def require_scan_calibration_chain(
    anchor_receipt: dict[str, Any], production_calibration: dict[str, Any],
    multi_calibration: dict[str, Any], production_calibration_sha256: str,
) -> None:
    """Refuse calibrations lacking per-claim thresholds or bound to another vault."""
    protocol = (multi_calibration.get("protocols") or {}).get("1x8") or {}
    try:
        false_accepts = int(production_calibration.get("per_claim_false_accepts", -1))
    except (TypeError, ValueError):
        false_accepts = -1
    if (
        production_calibration.get("per_claim_thresholds_status") != PER_CLAIM_PASS
        or false_accepts != 0
        or protocol.get("per_claim_thresholds_status") != PER_CLAIM_PASS
        or production_calibration.get("anchor_vectors_sha256")
        != anchor_receipt.get("combined_vectors_sha256")
        or production_calibration.get("model_manifest_sha256")
        != anchor_receipt.get("model_manifest_sha256")
        or multi_calibration.get("production_three_window_calibration_sha256")
        != production_calibration_sha256
    ):
        raise VoiceFirstRefused(
            "voice-first calibrations lack per-claim thresholds or are not bound to this vault"
        )


def filter_runs_by_word_gate(
    runs: Sequence[dict[str, Any]], words_by_source: dict[str, list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]], int]:
    """Drop runs with <5 transcript tokens before the per-member cap is applied."""
    kept = []
    for run in runs:
        words = words_for_interval(
            words_by_source[run["source_audio_sha256"]],
            int(run["source_start_frame"]), int(run["source_frame_count"]),
        )
        text_value = " ".join(str(row.get("word") or "").strip() for row in words).strip()
        if text_value and len(SMR.normalize_tokens(text_value)) >= 5:
            kept.append(run)
    return kept, len(runs) - len(kept)


def scan_status_for(window_rows: Sequence[dict[str, Any]]) -> str:
    """PASS only when at least one dense 3x window actually passed, not merely was scored."""
    if any(row.get("passes_3x_window") for row in window_rows):
        return "PASS"
    return "NO_PASSING_WINDOWS"


def run_scan(args: argparse.Namespace) -> int:
    inventory_path = Path(args.inventory)
    inventory_rows = SMR.read_jsonl(inventory_path, required=True)
    anchor_receipt = _read_json(Path(args.anchor_receipt))
    production_calibration = _read_json(Path(args.production_calibration))
    multi_calibration = _read_json(Path(args.multi_calibration))
    vault_path = Path(str(anchor_receipt.get("combined_vectors") or ""))
    model_manifest = Path(str(anchor_receipt.get("model_manifest") or ""))
    if (
        anchor_receipt.get("status") != "PASS"
        or not vault_path.is_file()
        or anchor_receipt.get("combined_vectors_sha256") != SMR.sha256_file(vault_path)
        or not model_manifest.is_file()
        or anchor_receipt.get("model_manifest_sha256") != SMR.sha256_file(model_manifest)
        or production_calibration.get("status") != "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
        or production_calibration.get("scoring_contract_sha256")
        != SAR.identity_scoring_contract_sha256()
        or multi_calibration.get("status") != "PASS_ZERO_FALSE_ACCEPT_ALL_PROTOCOLS"
        or multi_calibration.get("anchor_vectors_sha256")
        != anchor_receipt.get("combined_vectors_sha256")
    ):
        raise VoiceFirstRefused("voice-first anchor/calibration chain is stale")
    require_scan_calibration_chain(
        anchor_receipt, production_calibration, multi_calibration,
        SMR.sha256_file(Path(args.production_calibration)),
    )
    sources, source_skips = collect_sources(
        inventory_rows, Path(args.cnn_routes), Path(args.fox_root)
    )
    requested = {value.strip() for value in args.source.split(",") if value.strip()}
    if requested:
        sources = [row for row in sources if row["source_family"] in requested]
    if not sources:
        raise VoiceFirstRefused("no secured full-recording sources were selected")
    source_manifest = Path(args.source_manifest)
    SMR.write_jsonl_atomic(source_manifest, sources)

    output_root = Path(args.output_root)
    vault = SAR.load_vault(vault_path)
    anchors = np.asarray(vault["vectors"], dtype=np.float64)
    anchors /= np.maximum(np.linalg.norm(anchors, axis=1, keepdims=True), 1e-12)
    anchor_members = np.asarray(vault["members"]).astype(str)
    member_names = sorted(set(anchor_members.tolist()))
    member_indexes = {member: np.flatnonzero(anchor_members == member) for member in member_names}
    eligible_targets = set(anchor_receipt.get("eligible_targets") or [])
    gap_rows = SMR.read_jsonl(Path(args.member_gap), required=True)
    needed_by_member = {
        str(row.get("member") or ""): max(0, int(row.get("clips_needed") or 0))
        for row in gap_rows if row.get("target_recovery") is True
        and row.get("media_recovery_ready") is True
    }
    source_by_sha = {row["source_audio_sha256"]: row for row in sources}
    def embed_phase(
        phase: str, plans: dict[str, list[tuple[int, int]]]
    ) -> dict[str, dict[str, Any]]:
        embedding_root = output_root / (phase + "_embeddings")
        embedding_root.mkdir(parents=True, exist_ok=True)
        jobs = []
        receipts: dict[str, dict[str, Any]] = {}
        for source in sources:
            source_sha = source["source_audio_sha256"]
            windows = plans.get(source_sha) or []
            if not windows:
                continue
            plan_sha = SMR.sha256_json(windows)
            output = embedding_root / (source_sha + ".npz")
            receipt_path = output.with_suffix(".receipt.json")
            if receipt_path.is_file() and output.is_file():
                prior = _read_json(receipt_path)
                if (
                    prior.get("source_audio_sha256") == source_sha
                    and prior.get("embedding_sha256") == SMR.sha256_file(output)
                    and prior.get("model_manifest_sha256") == anchor_receipt["model_manifest_sha256"]
                    and prior.get("window_plan_sha256") == plan_sha
                    and prior.get("phase") == phase
                ):
                    receipts[source_sha] = prior
                    continue
            jobs.append({
                "source": source, "output": str(output), "batch_size": args.batch_size,
                "torch_threads": args.torch_threads, "windows": windows,
                "window_plan_sha256": plan_sha, "phase": phase,
            })
        if jobs:
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=max(1, int(args.workers)),
                initializer=_worker_init,
                initargs=(args.model_dir, str(model_manifest)),
            ) as executor:
                futures = {executor.submit(_embed_source, job): job for job in jobs}
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    job = futures[future]
                    result.update({
                        "schema": "speech-source-voice-first-embedding-receipt-v1",
                        "generated_at": SMR.utc_now(),
                        "model_manifest_sha256": anchor_receipt["model_manifest_sha256"],
                        "window_sec": WINDOW_SEC,
                        "grid_offsets_frames": list(GRID_OFFSETS),
                        "phase": phase,
                        "window_plan_sha256": job["window_plan_sha256"],
                        "planned_windows_embedded": True,
                    })
                    receipt_path = Path(result["embedding_path"]).with_suffix(".receipt.json")
                    SMR.write_json_atomic(receipt_path, result)
                    result["receipt"] = str(receipt_path.resolve())
                    result["receipt_sha256"] = SMR.sha256_file(receipt_path)
                    receipts[result["source_audio_sha256"]] = result
                    print(
                        f"EMBEDDED phase={phase} {result['source_audio_sha256'][:12]} "
                        f"windows={result['windows']}", flush=True,
                    )
        return receipts

    def score_phase(
        phase: str, embeddings: dict[str, dict[str, Any]],
        protocol: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows: list[dict[str, Any]] = []
        ledger: list[dict[str, Any]] = []
        for source in sources:
            source_sha = source["source_audio_sha256"]
            embedding = embeddings.get(source_sha)
            if not embedding:
                continue
            embedding_path = Path(embedding["embedding_path"])
            if embedding.get("embedding_sha256") != SMR.sha256_file(embedding_path):
                raise VoiceFirstRefused("voice-first embedding checkpoint changed")
            with np.load(embedding_path, allow_pickle=False) as value:
                starts = value["source_start_frame"].astype(np.int64)
                grids = value["grid_offset_frames"].astype(np.int64)
                vectors = value["vectors"].astype(np.float64)
                rms = value["rms"].astype(np.float64)
            vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
            similarity = vectors @ anchors.T
            best = np.column_stack([
                np.max(similarity[:, member_indexes[member]], axis=1)
                for member in member_names
            ])
            order = np.argsort(best, axis=1)
            winner_indexes = order[:, -1]
            runner_indexes = order[:, -2]
            score_path = output_root / "comparisons" / phase / (source_sha + ".npz")
            _save_npz_atomic(
                score_path, source_start_frame=starts, grid_offset_frames=grids,
                anchor_ids=vault["anchor_ids"], anchor_members=vault["members"],
                scores=similarity.astype(np.float32),
            )
            ledger.append({
                "phase": phase,
                "source_audio_sha256": source_sha,
                "comparison_matrix": str(score_path.resolve()),
                "comparison_matrix_sha256": SMR.sha256_file(score_path),
                "windows": int(starts.size),
                "anchors_per_window": int(anchors.shape[0]),
                "comparisons": int(starts.size * anchors.shape[0]),
            })
            for index, start in enumerate(starts.tolist()):
                winner_index = int(winner_indexes[index])
                runner_index = int(runner_indexes[index])
                winner = member_names[winner_index]
                winner_best = float(best[index, winner_index])
                runner_best = float(best[index, runner_index])
                margin = winner_best - runner_best
                winner_thresholds = (
                    protocol.get("per_claim_thresholds") or {}
                ).get(winner)
                threshold_valid = (
                    protocol.get("per_claim_thresholds_status")
                    == "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
                    and isinstance(winner_thresholds, dict)
                    and int(winner_thresholds.get("false_accepts", -1)) == 0
                )
                minimum_score = float(
                    winner_thresholds.get("minimum_target_best_score")
                    if threshold_valid else math.inf
                )
                minimum_margin = float(
                    winner_thresholds.get("minimum_best_anchor_margin")
                    if threshold_valid else math.inf
                )
                top = order[index, -5:][::-1]
                passed = bool(
                    threshold_valid and winner in eligible_targets and winner in needed_by_member
                    and winner_best >= minimum_score and margin >= minimum_margin
                )
                row = {
                    "schema": WINDOW_SCHEMA, "phase": phase,
                    "source_audio_sha256": source_sha,
                    "source_start_frame": int(start),
                    "source_frame_count": WINDOW_FRAMES,
                    "grid_offset_frames": int(grids[index]),
                    "rms": round(float(rms[index]), 9),
                    "winner": winner,
                    "winner_target_eligible": winner in eligible_targets,
                    "winner_profile_gap_target": winner in needed_by_member,
                    "winner_best": round(winner_best, 9),
                    "runner_up": member_names[runner_index],
                    "runner_up_best": round(runner_best, 9),
                    "best_anchor_margin": round(margin, 9),
                    "passes_protocol_window": passed,
                    "passes_3x_window": passed if phase == "dense-3x" else False,
                    "all_vaulted_speakers_compared": True,
                    "vaulted_speakers_compared": len(member_names),
                    "top_five": [
                        {"member": member_names[int(item)],
                         "best_anchor_score": round(float(best[index, item]), 9)}
                        for item in top
                    ],
                    "program_title_date_airtime_used_for_identity": False,
                }
                row["window_row_sha256"] = SAR._row_hash(row, "window_row_sha256")
                rows.append(row)
            print(f"SCORED phase={phase} {source_sha[:12]} windows={starts.size}", flush=True)
        return rows, ledger

    seed_plans = {
        source["source_audio_sha256"]: seeded_grid_windows(
            int(source["source_frames"]), args.seed_stride_sec
        ) for source in sources
    }
    seed_embeddings = embed_phase("seed-1x", seed_plans)
    one_window = multi_calibration["protocols"]["1x8"]
    seed_rows, seed_ledger = score_phase(
        "seed-1x", seed_embeddings, one_window,
    )
    accepted_seeds: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    for row in seed_rows:
        if row["passes_protocol_window"]:
            accepted_seeds[row["source_audio_sha256"]].append((
                int(row["grid_offset_frames"]), int(row["source_start_frame"])
            ))
    dense_plans = {
        source["source_audio_sha256"]: dense_windows_around_seeds(
            int(source["source_frames"]),
            accepted_seeds.get(source["source_audio_sha256"], []),
            args.expansion_radius_sec,
        ) for source in sources
    }
    dense_embeddings = embed_phase("dense-3x", dense_plans)
    window_rows, dense_ledger = score_phase(
        "dense-3x", dense_embeddings, production_calibration,
    )
    comparison_ledger = seed_ledger + dense_ledger

    window_manifest = Path(args.window_manifest)
    seed_manifest = window_manifest.with_name(window_manifest.stem + ".seed.jsonl")
    seed_rows.sort(key=lambda row: (
        row["source_audio_sha256"], row["grid_offset_frames"], row["source_start_frame"]
    ))
    SMR.write_jsonl_atomic(seed_manifest, seed_rows)
    window_rows.sort(key=lambda row: (
        row["source_audio_sha256"], row["grid_offset_frames"], row["source_start_frame"]
    ))
    SMR.write_jsonl_atomic(window_manifest, window_rows)
    raw_runs = passing_runs(window_rows, minimum_windows=3)
    words_by_source = {
        source_sha: _load_words(Path(source["asr_transcript"]))
        for source_sha, source in source_by_sha.items()
    }
    gated_runs, word_gate_rejected = filter_runs_by_word_gate(raw_runs, words_by_source)
    selected_runs = select_disjoint_profile_units(gated_runs, needed_by_member)
    candidates = []
    clip_root = output_root / "candidates"
    for run in selected_runs:
        source = source_by_sha[run["source_audio_sha256"]]
        start = int(run["source_start_frame"])
        count = int(run["source_frame_count"])
        words = words_for_interval(words_by_source[run["source_audio_sha256"]], start, count)
        text_value = " ".join(str(row.get("word") or "").strip() for row in words).strip()
        clip_id = (
            f"voice-first:{run['source_audio_sha256'][:20]}:{start}:{count}"
        )
        target = clip_root / run["member"].replace(" ", "_") / (SMR.sha256_text(clip_id) + ".wav")
        _write_pcm_slice(Path(source["source_wav"]), target, start, count)
        source_pcm = SMR.wav_pcm_slice_sha256(
            Path(source["source_wav"]), start, count, sample_rate=SAMPLE_RATE
        )
        staged_pcm = SMR.wav_pcm_slice_sha256(target, 0, count, sample_rate=SAMPLE_RATE)
        if source_pcm != staged_pcm:
            raise VoiceFirstRefused("voice-first candidate differs from source PCM")
        source_offset = float(source.get("source_offset_sec") or 0.0)
        candidate = {
            "schema": SMR.CLIP_SCHEMA,
            "clip_id": clip_id,
            "member": run["member"],
            "source_family": source["source_family"],
            "show": source["show"],
            "date": source["date"],
            "package": source["package"],
            "turn_id": f"voice-first-asr:{run['source_audio_sha256'][:20]}:{start}:{start + count}",
            "source_expected_turn_rowids": [],
            "transcript_path": source["asr_transcript"] + f"#frames={start}:{start + count}",
            "text_sha256": SMR.sha256_text(text_value),
            "transcript_text": text_value,
            "source_audio_sha256": run["source_audio_sha256"],
            "source_wav": source["source_wav"],
            "source_receipt": source["source_receipt"],
            "source_receipt_sha256": source["source_receipt_sha256"],
            "reference_url": source["reference_url"],
            "asr_transcript": source["asr_transcript"],
            "asr_transcript_sha256": source["asr_transcript_sha256"],
            "asr_transcript_receipt": source["asr_transcript_receipt"],
            "asr_transcript_receipt_sha256": source["asr_transcript_receipt_sha256"],
            "provider": source["provider"],
            "media_id": source["media_id"],
            "binding_method": "full-recording-exhaustive-voice-first-then-whisper-word-attachment-v1",
            "identity_claim_source": "voice-first-exhaustive-all-vault-window-winner",
            "voice_first_scan_receipt": str(Path(args.scan_receipt).resolve()),
            "voice_first_window_row_sha256s": run["window_row_sha256s"],
            "alignment_ratio": 1.0,
            "start_sec": round(start / SAMPLE_RATE + source_offset, 6),
            "end_sec": round((start + count) / SAMPLE_RATE + source_offset, 6),
            "acquired_clip_start_sec": round(start / SAMPLE_RATE, 6),
            "source_offset_sec": round(source_offset, 6),
            "duration_sec": round(count / SAMPLE_RATE, 6),
            "source_sample_rate": SAMPLE_RATE,
            "source_start_frame": start,
            "source_frame_count": count,
            "source_slice_pcm_sha256": source_pcm,
            "staged_pcm_sha256": staged_pcm,
            "words": len(SMR.normalize_tokens(text_value)),
            "wav": str(target.resolve()),
            "audio_sha256": SMR.sha256_file(target),
            "sample_rate": SAMPLE_RATE,
            "status": "CANDIDATE_AUDIT_REQUIRED",
            "speaker_identity_status": "INDEPENDENT_EXHAUSTIVE_MULTI_ANCHOR_RECHECK_REQUIRED",
            "subject": classify_subject(text_value),
            "subject_method": "post-identity-deterministic-transcript-keyword-v1",
            "setting": (
                "podcast_interview" if "podcast" in source["source_kind"]
                else "broadcast_news_interview"
            ),
            "features": {},
        }
        candidate["cut_binding_sha256"] = SMR.candidate_cut_binding(candidate)
        candidate["clip_sha256"] = SMR._candidate_hash(candidate)
        candidates.append(candidate)

    candidate_manifest = Path(args.candidate_manifest)
    candidates.sort(key=lambda row: row["clip_id"])
    SMR.write_jsonl_atomic(candidate_manifest, candidates)
    candidate_receipt = {
        "schema": SMR.CANDIDATE_RECEIPT_SCHEMA,
        "status": "AUDIT_REQUIRED",
        "generated_at": SMR.utc_now(),
        "manifest": str(candidate_manifest.resolve()),
        "manifest_sha256": SMR.sha256_file(candidate_manifest),
        "candidate_clips": len(candidates),
        "clip_schema": SMR.CLIP_SCHEMA,
        "clip_schema_counts": {SMR.CLIP_SCHEMA: len(candidates)},
        "exact_frame_candidates": len(candidates),
        "inventory": str(inventory_path.resolve()),
        "inventory_sha256": SMR.sha256_file(inventory_path),
        "minimum_alignment_ratio": 0.68,
        "exact_frame_cut_binding_required": True,
        "voice_first_before_cut": True,
        "voice_first_window_manifest_sha256": SMR.sha256_file(window_manifest),
        "voice_first_scan_receipt": str(Path(args.scan_receipt).resolve()),
        "cleanup_authorized": False,
    }
    SMR.write_json_atomic(Path(args.candidate_receipt), candidate_receipt)
    scan_status = scan_status_for(window_rows)
    scan_receipt = {
        "schema": SCAN_RECEIPT_SCHEMA,
        "status": scan_status,
        "generated_at": SMR.utc_now(),
        "source_manifest": str(source_manifest.resolve()),
        "source_manifest_sha256": SMR.sha256_file(source_manifest),
        "sources": len(sources),
        **source_skips,
        "runs_rejected_by_word_gate": word_gate_rejected,
        "source_audio_hours": round(sum(float(row["duration_sec"]) for row in sources) / 3600, 6),
        "window_manifest": str(window_manifest.resolve()),
        "window_manifest_sha256": SMR.sha256_file(window_manifest),
        "seed_window_manifest": str(seed_manifest.resolve()),
        "seed_window_manifest_sha256": SMR.sha256_file(seed_manifest),
        "full_recording_seed_windows": len(seed_rows),
        "accepted_one_window_discovery_seeds": sum(
            row["passes_protocol_window"] is True for row in seed_rows
        ),
        "dense_expansion_windows": len(window_rows),
        "seed_stride_sec": int(args.seed_stride_sec),
        "dense_expansion_radius_sec": int(args.expansion_radius_sec),
        "all_planned_windows_embedded": True,
        "every_complete_8s_window_embedded": False,
        "discovery_method": (
            "uniform two-grid full-recording 1x8 seed scan followed by dense adjacent "
            "3x8 expansion around zero-false-accept seeds"
        ),
        "grid_offsets_frames": list(GRID_OFFSETS),
        "all_vaulted_speakers_compared": True,
        "vaulted_speakers": len(member_names),
        "anchor_vectors_sha256": anchor_receipt["combined_vectors_sha256"],
        "production_calibration_sha256": SMR.sha256_file(Path(args.production_calibration)),
        "multi_calibration_sha256": SMR.sha256_file(Path(args.multi_calibration)),
        "scoring_contract_sha256": SAR.identity_scoring_contract_sha256(),
        "comparison_ledger": comparison_ledger,
        "comparison_ledger_sha256": SMR.sha256_json(comparison_ledger),
        "comparisons": sum(int(row["comparisons"]) for row in comparison_ledger),
        "passing_three_window_chunks_before_profile_cap": len(raw_runs),
        "selected_profile_units": len(candidates),
        "candidate_manifest": str(candidate_manifest.resolve()),
        "candidate_manifest_sha256": SMR.sha256_file(candidate_manifest),
        "candidate_receipt": str(Path(args.candidate_receipt).resolve()),
        "candidate_receipt_sha256": SMR.sha256_file(Path(args.candidate_receipt)),
        "identity_precedes_cut": True,
        "transcript_attached_after_identity": True,
        "program_title_date_airtime_used_for_identity": False,
        "source_audio_cleanup_authorized": False,
    }
    SMR.write_json_atomic(Path(args.scan_receipt), scan_receipt)
    print(json.dumps({
        "status": scan_status, "sources": len(sources), **source_skips,
        "windows": len(window_rows), "runs_rejected_by_word_gate": word_gate_rejected,
        "raw_three_window_units": len(raw_runs), "selected_candidates": len(candidates),
        "candidate_manifest_sha256": SMR.sha256_file(candidate_manifest),
    }, sort_keys=True))
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("calibrate", "expand-vault", "scan"))
    parser.add_argument("--anchor-receipt", default=str(DEFAULT_ANCHOR_RECEIPT))
    parser.add_argument("--production-calibration", default=str(DEFAULT_CALIBRATION))
    parser.add_argument("--multi-calibration", default=str(DEFAULT_MULTI_CALIBRATION))
    parser.add_argument("--all-verified-manifest", default=str(DEFAULT_ALL_VERIFIED_MANIFEST))
    parser.add_argument("--all-verified-receipt", default=str(DEFAULT_ALL_VERIFIED_RECEIPT))
    parser.add_argument("--all-verified-vectors", default=str(DEFAULT_ALL_VERIFIED_VECTORS))
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY))
    parser.add_argument("--cnn-routes", default=str(DEFAULT_CNN_ROUTES))
    parser.add_argument("--fox-root", default=str(DEFAULT_FOX_ROOT))
    parser.add_argument("--member-gap", default=str(DEFAULT_GAP))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--source-manifest", default=str(DEFAULT_SOURCE_MANIFEST))
    parser.add_argument("--window-manifest", default=str(DEFAULT_WINDOW_MANIFEST))
    parser.add_argument("--scan-receipt", default=str(DEFAULT_SCAN_RECEIPT))
    parser.add_argument(
        "--candidate-manifest", default=str(DEFAULT_OUTPUT_ROOT / "candidate_clips.jsonl")
    )
    parser.add_argument(
        "--candidate-receipt", default=str(DEFAULT_OUTPUT_ROOT / "candidate_clips.receipt.json")
    )
    parser.add_argument("--model-dir", default=str(SAR.DEFAULT_ECAPA_MODEL))
    parser.add_argument("--source", default="cnn,fox")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--torch-threads", type=int, default=1)
    parser.add_argument("--seed-stride-sec", type=int, default=64)
    parser.add_argument("--expansion-radius-sec", type=int, default=96)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.action == "calibrate":
        return run_calibrate(args)
    if args.action == "expand-vault":
        return run_expand_vault(args)
    return run_scan(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VoiceFirstRefused, SAR.IdentityRefused, SMR.RecoveryError, OSError, ValueError) as exc:
        print(f"ERROR {exc}", file=__import__("sys").stderr)
        raise SystemExit(2)
