"""Fail-closed reader for the display-only descriptive speech artifact."""
from __future__ import annotations
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import to_host  # noqa: E402

import hashlib
import json
import math
import os
from pathlib import Path


SCHEMA = "speech-descriptive-v1"
STATUS = "DISPLAY_ONLY_NO_ENGINE_WIRING"
RECEIPT_SCHEMA = "speech-descriptive-receipt-v1"
CHANNELS = ("pause", "pace", "volume_dyn", "pitch_var", "strain", "f0_mean")


class SpeechContractError(RuntimeError):
    pass


def _read(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, TypeError, ValueError) as exc:
        raise SpeechContractError("speech artifact %s is unreadable or invalid JSON: %s" %
                                  (path, exc)) from exc
    if not isinstance(value, dict):
        raise SpeechContractError("speech artifact %s must contain an object" % path)
    return value


def _sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _number(value, label, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SpeechContractError("speech artifact %s must be a finite number" % label)
    if minimum is not None and value < minimum:
        raise SpeechContractError("speech artifact %s must be >= %s" % (label, minimum))


def _count(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SpeechContractError("speech artifact %s must be a nonnegative integer" % label)
    return value


def _validate_block(block, label):
    if not isinstance(block, dict):
        raise SpeechContractError("speech artifact %s must be an object" % label)
    for key in ("clips", "minutes", "episodes"):
        _number(block.get(key), "%s.%s" % (label, key), minimum=0)
    for channel in CHANNELS:
        for suffix in ("_mean", "_sd"):
            value = block.get(channel + suffix)
            if value is not None:
                _number(value, "%s.%s%s" % (label, channel, suffix))


def validate(payload, receipt, artifact_path: Path):
    if payload.get("schema") != SCHEMA or payload.get("status") != STATUS:
        raise SpeechContractError("speech artifact schema/status is not the approved descriptive contract")
    contract = payload.get("contract")
    required = {
        "display_only": True,
        "audio_may_modify_big_five": False,
        "audio_may_modify_rank_or_allocation": False,
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
    }
    if not isinstance(contract, dict) or any(contract.get(k) is not v for k, v in required.items()):
        raise SpeechContractError("speech artifact violates the display-only/no-engine contract")
    corpus = payload.get("corpus")
    if not isinstance(corpus, dict):
        raise SpeechContractError("speech artifact corpus must be an object")
    for key in (
            "manifest_rows", "prepared_rows", "excluded_rows", "superseded_rows",
            "members", "hours"):
        _number(corpus.get(key), "corpus.%s" % key, minimum=0)
    if (corpus["manifest_rows"] != corpus["prepared_rows"] + corpus["superseded_rows"]
            or corpus["excluded_rows"] != corpus["superseded_rows"] or
            corpus.get("cut_status") != "ALL_HASH_AND_PCM_VERIFIED"):
        raise SpeechContractError("speech artifact does not prepare the complete accepted manifest")
    if not isinstance(corpus.get("by_source_family"), dict) or not isinstance(
            corpus.get("by_subject"), dict):
        raise SpeechContractError("speech artifact lacks source/subject coverage metadata")
    _number(corpus.get("unlabeled_subject_clips"), "corpus.unlabeled_subject_clips", minimum=0)
    verified = ((payload.get("source") or {}).get("source_media_verified_final"))
    if not isinstance(verified, dict) or not isinstance(verified.get("enabled"), bool):
        raise SpeechContractError("speech artifact lacks verified-final source metadata")
    accepted_verified_clips = _count(
        verified.get("accepted_clips"), "source.verified.accepted_clips")
    if verified["enabled"]:
        for key in (
            "manifest_sha256", "receipt_sha256", "identity_manifest_sha256",
            "identity_receipt_sha256", "candidate_manifest_sha256",
            "candidate_receipt_sha256", "inventory_sha256", "calibration_sha256",
            "scoring_contract_sha256",
        ):
            value = verified.get(key)
            if not isinstance(value, str) or len(value) != 64:
                raise SpeechContractError("speech verified-final chain lacks %s" % key)
        by_source = verified.get("by_source")
        if not isinstance(by_source, dict):
            raise SpeechContractError("speech verified-final source ledger is invalid")
        verified_source_counts = {}
        for family, row in by_source.items():
            if not isinstance(family, str) or not family or not isinstance(row, dict):
                raise SpeechContractError("speech verified-final source ledger is malformed")
            verified_source_counts[family] = _count(
                row.get("verified_final_clips"),
                "source.verified.by_source.%s.verified_final_clips" % family,
            )
        if (
            verified.get("features_recomputed_from_every_final_wav") is not True
            or verified.get("candidate_features_trusted") is not False
            or verified.get("source_pcm_reverified") is not True
            or verified.get("cumulative_source_bindings_complete") is not True
            or sum(verified_source_counts.values()) != accepted_verified_clips
        ):
            raise SpeechContractError("speech verified-final measurement/PCM contract is invalid")
    else:
        verified_source_counts = {}
    source = payload.get("source") or {}
    lineage = source.get("canonical_source_lineage")
    supersession = source.get("canonical_supersession")
    if not isinstance(lineage, dict) or not isinstance(supersession, dict):
        raise SpeechContractError("speech artifact lacks canonical source-lineage supersession metadata")
    supersession_rows = supersession.get("records")
    if (
        supersession.get("schema") != "speech-canonical-record-supersession-v1"
        or supersession.get("physical_cleanup_authorized") is not False
        or not isinstance(supersession_rows, list)
        or int(supersession.get("superseded_rows", -1)) != len(supersession_rows)
        or len(supersession_rows) != corpus["superseded_rows"]
    ):
        raise SpeechContractError("speech canonical supersession partition is invalid")
    cnn_verified = any(
        count > 0 and (
            family.lower() == "cnn"
            or family.lower().startswith("cnn-")
            or "axe" in family.lower()
        )
        for family, count in verified_source_counts.items()
    )
    if cnn_verified and (
        lineage.get("enabled") is not True
        or lineage.get("required") is not True
        or not isinstance(lineage.get("manifest_sha256"), str)
        or len(lineage["manifest_sha256"]) != 64
        or not isinstance(lineage.get("receipt_sha256"), str)
        or len(lineage["receipt_sha256"]) != 64
    ):
        raise SpeechContractError("verified CNN clips lack canonical full-source lineage")
    for row in supersession_rows:
        superseded_by = row.get("superseded_by") if isinstance(row, dict) else None
        if (
            not isinstance(row, dict)
            or row.get("action") != "CANONICAL_RECORD_SUPERSEDED_SOURCE_INTERVAL_OVERLAP"
            or row.get("physical_cleanup_authorized") is not False
            or not Path(to_host(str(row.get("out_wav") or ""))).is_absolute()
            or not Path(to_host(str(row.get("source_wav") or ""))).is_absolute()
            or not isinstance(row.get("source_audio_sha256"), str)
            or len(row["source_audio_sha256"]) != 64
            or not isinstance(superseded_by, list)
            or not superseded_by
            or any(not isinstance(item.get("reference_url"), str)
                   or not item["reference_url"].startswith(("http://", "https://"))
                   or int(item.get("overlap_frames", 0)) <= 0
                   for item in superseded_by)
        ):
            raise SpeechContractError("speech canonical supersession record is invalid")
    members = payload.get("members")
    if not isinstance(members, dict) or len(members) != corpus["members"]:
        raise SpeechContractError("speech artifact member count does not match corpus summary")
    clip_total = 0
    for name, row in members.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(row, dict):
            raise SpeechContractError("speech artifact contains a malformed member row")
        coverage = row.get("coverage")
        if not isinstance(coverage, dict):
            raise SpeechContractError("speech artifact member %r lacks coverage" % name)
        _number(coverage.get("clips"), "%s.coverage.clips" % name, minimum=1)
        _number(coverage.get("minutes"), "%s.coverage.minutes" % name, minimum=0)
        if coverage.get("cut_status") != "ALL_HASH_AND_PCM_VERIFIED":
            raise SpeechContractError("speech artifact member %r has unverified cuts" % name)
        clip_total += coverage["clips"]
        if row.get("headline_interviews_only") is not None:
            _validate_block(row["headline_interviews_only"], "%s.headline" % name)
        settings = row.get("by_setting")
        if not isinstance(settings, dict) or not settings:
            raise SpeechContractError("speech artifact member %r lacks setting coverage" % name)
        for setting, block in settings.items():
            _validate_block(block, "%s.by_setting.%s" % (name, setting))
        sources = row.get("by_source")
        subjects = row.get("by_subject")
        if not isinstance(sources, dict) or not sources or not isinstance(subjects, dict):
            raise SpeechContractError("speech artifact member %r lacks source/subject metadata" % name)
        for source, block in sources.items():
            _validate_block(block, "%s.by_source.%s" % (name, source))
        for subject, block in subjects.items():
            _validate_block(block, "%s.by_subject.%s" % (name, subject))
        if not isinstance(row.get("subject_tonality_contrasts"), list):
            raise SpeechContractError(
                "speech artifact member %r lacks labeled-subject tonality contrasts" % name
            )
    if clip_total != corpus["prepared_rows"]:
        raise SpeechContractError("speech artifact member coverage does not partition the corpus")
    if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("status") != "PASS":
        raise SpeechContractError("speech receipt is not PASS")
    if receipt.get("artifact_sha256") != _sha256(artifact_path):
        raise SpeechContractError("speech receipt is not bound to the current artifact")
    if (receipt.get("manifest_rows") != corpus["manifest_rows"] or
            receipt.get("prepared_rows") != corpus["prepared_rows"] or
            receipt.get("excluded_rows") != corpus["excluded_rows"] or
            receipt.get("superseded_rows") != corpus["superseded_rows"] or
            receipt.get("canonical_supersession_rows_sha256")
            != supersession.get("supersession_rows_sha256") or
            (lineage.get("enabled") and receipt.get("canonical_source_lineage_receipt_sha256")
             != lineage.get("receipt_sha256")) or receipt.get("contract") != contract):
        raise SpeechContractError("speech receipt disagrees with the descriptive artifact")
    if (
        receipt.get("verified_final_manifest_rows") != verified["accepted_clips"]
        or (verified["enabled"] and (
            receipt.get("verified_final_audit_status") != "PASS"
            or receipt.get("verified_final_manifest_sha256") != verified.get("manifest_sha256")
            or receipt.get("verified_final_receipt_sha256") != verified.get("receipt_sha256")
        ))
    ):
        raise SpeechContractError("speech receipt disagrees with the verified-final chain")
    return payload


def load_optional(root, artifact_path=None, receipt_path=None):
    root = Path(root)
    artifact_path = Path(artifact_path or os.environ.get("SPEECH_DESCRIPTIVE_PATH") or
                         root / "data" / "speech" / "speech_descriptive_v1.json")
    if not artifact_path.exists():
        return None
    receipt_path = Path(receipt_path or os.environ.get("SPEECH_DESCRIPTIVE_RECEIPT_PATH") or
                        root / "data" / "speech" / "speech_descriptive_v1.receipt.json")
    if not receipt_path.exists():
        raise SpeechContractError("speech artifact is present without its PASS receipt: %s" % receipt_path)
    return validate(_read(artifact_path), _read(receipt_path), artifact_path)
