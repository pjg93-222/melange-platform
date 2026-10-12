"""Fail-closed speaker identification and final cutting for recovered broadcasts.

The source-media lane creates a Whisper word transcript and lexically aligns
stored crawler turns.  That establishes *what was said and when*, not who said
it.  This module performs the separate identity step requested for the expanded
corpus:

1. build a hash-bound vault of previously accepted, solo-speaker references;
2. embed multiple cross-event references for every eligible target;
3. compare every candidate speech window with every vaulted speaker;
4. accept only unanimous, zero-false-accept-calibrated structural consensus;
5. cut a final PCM16 WAV from the source and retain its public reference URL.

``100%`` is an operational evidence label here, never a probability claim.  A
missing anchor, weak margin, single dissenting window, stale hash, or incomplete
calibration always remains unresolved.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
from typing import Any, Callable, Iterable, Sequence
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fec.members.b5_audio_embed import BackendRefused, cosine, get_backend
from fec.speech import source_media_recovery as SMR


ROOT = SMR.ROOT
VERIFY_PATH = ROOT / "data" / "members" / "b5_audio_verify.json"
AUDIO_ROOT = ROOT / "audio_pilot"
ANCHOR_MANIFEST = ROOT / "data" / "speech" / "speaker_anchor_vault_v1.jsonl"
ANCHOR_RECEIPT = ROOT / "data" / "speech" / "speaker_anchor_vault_v1.receipt.json"
CALIBRATION_PATH = ROOT / "data" / "speech" / "speaker_anchor_calibration_v1.json"
DEFAULT_MEDIA_ROOT = SMR.DEFAULT_MEDIA_ROOT
DEFAULT_VAULT_ROOT = Path(r"C:\melange-data\speaker_anchor_vault_v1")
DEFAULT_ECAPA_MODEL = Path(r"C:\melange-data\models\ecapa-voxceleb")
DEFAULT_ECAPA_MANIFEST = ROOT / "data" / "speech" / "ecapa_model_manifest_v2.json"
DECISION_MANIFEST = ROOT / "data" / "speech" / "source_media_identity_v2.jsonl"
DECISION_RECEIPT = ROOT / "data" / "speech" / "source_media_identity_v2.receipt.json"
FINAL_MANIFEST = ROOT / "data" / "speech" / "source_media_verified_v2.jsonl"
FINAL_RECEIPT = ROOT / "data" / "speech" / "source_media_verified_v2.receipt.json"
DEFAULT_FINAL_ROOT = Path(r"C:\melange-data\source_media_verified_v2")
CLEANUP_MANIFEST = ROOT / "data" / "speech" / "source_media_cleanup_v2.jsonl"
CLEANUP_RECEIPT = ROOT / "data" / "speech" / "source_media_cleanup_v2.receipt.json"

ANCHOR_SCHEMA = "speech-speaker-anchor-vault-v1"
ANCHOR_RECEIPT_SCHEMA = "speech-speaker-anchor-vault-receipt-v1"
CALIBRATION_SCHEMA = "speech-speaker-anchor-calibration-v2"
IDENTITY_SCHEMA = "speech-source-speaker-identity-v3"
IDENTITY_RECEIPT_SCHEMA = "speech-source-speaker-identity-receipt-v3"
FINAL_SCHEMA = "speech-source-verified-clip-v3"
FINAL_RECEIPT_SCHEMA = "speech-source-verified-clip-receipt-v3"
CLEANUP_SCHEMA = "speech-source-cleanup-v3"
CLEANUP_RECEIPT_SCHEMA = "speech-source-cleanup-receipt-v3"
SOURCE_PRIORITY = ("abc-this-week", "fox", "msnow", "cnn")
MIN_TARGET_ANCHORS = 3
MIN_TARGET_EVENTS = 3
MIN_TARGET_SECONDS = 180.0
MIN_CANDIDATE_WINDOWS = 3
CALIBRATION_WINDOWS_PER_EVENT = 3
CALIBRATION_WINDOW_SEC = 8.0
LOCKED_MINIMUM_ALIGNMENT_RATIO = 0.68
COMPACT_UNRESOLVED_REASON = "candidate has fewer than three full speech windows"
COMPACT_UNRESOLVED_FIELDS = frozenset({
    "schema", "clip_id", "claimed_member", "status", "reasons",
    "operational_confidence_label", "probability_claim",
    "scoring_contract_sha256", "identified_at", "identity_row_sha256",
})


class IdentityRefused(RuntimeError):
    """The evidence contract was not fully satisfied."""


def validate_locked_alignment_threshold(value: Any) -> float:
    try:
        ratio = float(value)
    except (TypeError, ValueError) as exc:
        raise IdentityRefused("locked lexical-alignment threshold is missing") from exc
    if (
        not math.isfinite(ratio)
        or ratio < LOCKED_MINIMUM_ALIGNMENT_RATIO
        or ratio > 1.0
    ):
        raise IdentityRefused("locked lexical-alignment threshold is invalid or weakened")
    return ratio


def identity_scoring_contract() -> dict[str, Any]:
    return {
        "schema": "speech-speaker-identity-scoring-contract-v4",
        "candidate_window_sec": CALIBRATION_WINDOW_SEC,
        "candidate_tail_policy": "discard every trailing fragment shorter than one exact window",
        "minimum_candidate_windows": MIN_CANDIDATE_WINDOWS,
        "target_aggregation": "maximum cosine across every anchor for the claimed speaker",
        "competitor_aggregation": "maximum cosine for every other vaulted speaker",
        "unique_winner_required": True,
        "threshold_scope": (
            "per claimed speaker; each claim threshold is calibrated against every "
            "held-out event belonging to every other vaulted speaker"
        ),
        "score_comparison": (
            "target_best >= persisted per-claim minimum_target_best_score"
        ),
        "margin_comparison": (
            "target_best - competitor_best >= persisted per-claim "
            "minimum_best_anchor_margin"
        ),
        "sequence_aggregation": "every disjoint candidate window must pass independently",
        "accepted_run_extraction": (
            "maximal consecutive passing runs of at least three windows may be cut; "
            "nonpassing windows are never included"
        ),
        "whole_candidate_acceptance": (
            "allowed only when exact windows cover every candidate PCM frame; "
            "an untested trailing fragment forces exact-run-only partial acceptance"
        ),
        "all_vaulted_speakers_compared": True,
        "program_title_date_airtime_used": False,
    }


def identity_scoring_contract_sha256() -> str:
    return SMR.sha256_json(identity_scoring_contract())


def source_priority(value: str) -> tuple[int, str]:
    try:
        return SOURCE_PRIORITY.index(value), value
    except ValueError:
        return len(SOURCE_PRIORITY), value


def ordered_source_families(values: Iterable[str]) -> list[str]:
    return sorted(set(values), key=source_priority)


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _read_verify(path: Path) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IdentityRefused(f"invalid anchor verification ledger: {path}") from exc
    if not isinstance(value, dict):
        raise IdentityRefused("anchor verification ledger root is not an object")
    return {str(key): row for key, row in value.items() if isinstance(row, dict)}


def accepted_anchor_metadata(row: dict[str, Any]) -> bool:
    return (
        row.get("speaker") == "SOLO_TARGET"
        and row.get("identity") == "MATCH"
        and row.get("keep") is True
    )


def anchor_event_id(path: Path) -> str:
    value = re.sub(r"^clip_+", "", path.stem, flags=re.IGNORECASE)
    if not value:
        raise IdentityRefused(f"anchor has no event ID: {path}")
    return value


def wav_contract(path: Path) -> tuple[float, int]:
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.getnframes()
    except (OSError, wave.Error) as exc:
        raise IdentityRefused(f"invalid anchor WAV: {path}") from exc
    if (channels, width, rate) != (1, 2, 16000):
        raise IdentityRefused(f"anchor is not mono 16-kHz PCM16: {path}")
    return frames / float(rate), frames


def build_anchor_manifest(
    verify_path: Path = VERIFY_PATH,
    audio_root: Path = AUDIO_ROOT,
    *,
    hash_audio: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return every accepted reference and the stricter target-eligible cohort."""
    rows: list[dict[str, Any]] = []
    refused: list[dict[str, str]] = []
    for relative, evidence in sorted(_read_verify(verify_path).items()):
        if not accepted_anchor_metadata(evidence):
            continue
        rel_path = Path(relative)
        member = rel_path.parts[0] if len(rel_path.parts) > 1 else rel_path.parent.name
        path = rel_path if rel_path.is_absolute() else audio_root / rel_path
        try:
            if not member or not path.is_file() or not _within(path, audio_root):
                raise IdentityRefused("missing anchor or path outside the declared vault")
            duration, frames = wav_contract(path)
            if duration < 4.0:
                raise IdentityRefused("anchor is shorter than four seconds")
            digest = SMR.sha256_file(path) if hash_audio else None
            row = {
                "schema": ANCHOR_SCHEMA,
                "anchor_id": "anchor:" + SMR.sha256_text(relative)[:24],
                "member": member,
                "event_id": anchor_event_id(path),
                "path": str(path.resolve()),
                "relative_path": relative.replace("\\", "/"),
                "duration_sec": round(duration, 6),
                "frames": frames,
                "audio_sha256": digest,
                "label_contract": {
                    "speaker": "SOLO_TARGET",
                    "identity": "MATCH",
                    "keep": True,
                    "source": "accepted-existing-voice-reference",
                    "evidence": str(evidence.get("evidence") or ""),
                },
            }
            row["anchor_row_sha256"] = SMR.sha256_json(row)
            rows.append(row)
        except IdentityRefused as exc:
            refused.append({"path": relative, "reason": str(exc)})

    by_member: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_member[row["member"]].append(row)
    eligible: list[str] = []
    coverage: dict[str, dict[str, Any]] = {}
    for member, anchors in sorted(by_member.items()):
        events = {row["event_id"] for row in anchors}
        seconds = sum(float(row["duration_sec"]) for row in anchors)
        is_eligible = (
            len(anchors) >= MIN_TARGET_ANCHORS
            and len(events) >= MIN_TARGET_EVENTS
            and seconds >= MIN_TARGET_SECONDS
        )
        if is_eligible:
            eligible.append(member)
        coverage[member] = {
            "anchors": len(anchors),
            "events": len(events),
            "duration_sec": round(seconds, 6),
            "target_eligible": is_eligible,
        }
    for row in rows:
        row["target_eligible"] = row["member"] in set(eligible)
        body = dict(row)
        body.pop("anchor_row_sha256", None)
        row["anchor_row_sha256"] = SMR.sha256_json(body)
    summary = {
        "accepted_anchors": len(rows),
        "vaulted_speakers": len(by_member),
        "eligible_target_speakers": len(eligible),
        "eligible_targets": eligible,
        "duration_hours": round(sum(float(row["duration_sec"]) for row in rows) / 3600.0, 6),
        "minimum_target_anchors": MIN_TARGET_ANCHORS,
        "minimum_target_events": MIN_TARGET_EVENTS,
        "minimum_target_seconds": MIN_TARGET_SECONDS,
        "refused_references": refused,
        "coverage": coverage,
    }
    return rows, summary


def persist_anchor_manifest(
    rows: Sequence[dict[str, Any]], summary: dict[str, Any],
    manifest_path: Path = ANCHOR_MANIFEST, receipt_path: Path = ANCHOR_RECEIPT,
) -> dict[str, Any]:
    SMR.write_jsonl_atomic(manifest_path, rows)
    receipt = {
        "schema": ANCHOR_RECEIPT_SCHEMA,
        "status": "REFERENCES_HASHED_EMBEDDINGS_REQUIRED",
        "generated_at": SMR.utc_now(),
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": SMR.sha256_file(manifest_path),
        **summary,
    }
    SMR.write_json_atomic(receipt_path, receipt)
    return receipt


def select_reference_window(
    samples: np.ndarray, sample_rate: int, seconds: float = 20.0
) -> tuple[np.ndarray, float, float]:
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if sample_rate != 16000 or samples.size < int(4.0 * sample_rate):
        raise IdentityRefused("reference audio violates the 16-kHz/four-second floor")
    width = min(samples.size, int(seconds * sample_rate))
    if samples.size == width:
        return samples, 0.0, samples.size / float(sample_rate)
    hop = max(sample_rate, width // 4)
    starts = list(range(0, samples.size - width + 1, hop))
    if starts[-1] != samples.size - width:
        starts.append(samples.size - width)
    scored = []
    for start in starts:
        signal = samples[start : start + width]
        rms = float(np.sqrt(np.mean(np.square(signal, dtype=np.float64))))
        clipping = float(np.mean(np.abs(signal) >= 0.999))
        scored.append((rms - clipping, -start, start))
    start = max(scored)[2]
    return samples[start : start + width], start / sample_rate, (start + width) / sample_rate


def select_calibration_windows(
    samples: np.ndarray,
    sample_rate: int,
    *,
    window_sec: float = CALIBRATION_WINDOW_SEC,
    windows_per_event: int = CALIBRATION_WINDOWS_PER_EVENT,
) -> tuple[list[np.ndarray], float, float]:
    """Return candidate-duration windows from one verified anchor event.

    Calibration must exercise the same short-window domain as production
    candidates.  The containing reference span is selected once, then split
    into non-overlapping windows so a single transient cannot manufacture
    unanimous evidence.
    """
    if windows_per_event < MIN_CANDIDATE_WINDOWS or window_sec < 4.0:
        raise IdentityRefused("calibration window protocol violates the evidence floor")
    seconds = float(window_sec) * int(windows_per_event)
    reference, start_sec, end_sec = select_reference_window(
        samples, sample_rate, seconds=seconds
    )
    width = int(round(float(window_sec) * sample_rate))
    required = width * int(windows_per_event)
    if reference.size < required:
        raise IdentityRefused("anchor is too short for candidate-domain calibration windows")
    windows = [
        np.asarray(reference[index * width : (index + 1) * width], dtype=np.float32)
        for index in range(int(windows_per_event))
    ]
    return windows, start_sec, end_sec


def _load_audio(path: Path) -> tuple[np.ndarray, int]:
    try:
        import soundfile as sf
    except ImportError as exc:
        raise IdentityRefused("soundfile is required for speaker anchors") from exc
    signal, rate = sf.read(str(path), dtype="float32", always_2d=False)
    signal = np.asarray(signal, dtype=np.float32)
    if signal.ndim == 2:
        signal = signal.mean(axis=1)
    return signal.reshape(-1), int(rate)


def _save_npy_atomic(path: Path, vector: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp = Path(raw)
    try:
        with temp.open("wb") as handle:
            np.save(handle, np.asarray(vector, dtype=np.float32), allow_pickle=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _primary_anchor_cache_path(row: dict[str, Any], backend_name: str, vault_root: Path) -> Path:
    key = SMR.sha256_text(
        str(row.get("audio_sha256") or "") + "|" + backend_name + "|highest-rms-20s-v1"
    )
    return vault_root / backend_name / (key + ".npy")


def _calibration_anchor_cache_paths(
    row: dict[str, Any], backend_name: str, vault_root: Path
) -> list[Path]:
    protocol = (
        f"candidate-domain-calibration-v2|{CALIBRATION_WINDOW_SEC}|"
        f"{CALIBRATION_WINDOWS_PER_EVENT}"
    )
    return [
        vault_root / backend_name / (
            SMR.sha256_text(
                str(row.get("audio_sha256") or "")
                + "|" + backend_name + "|" + protocol + f"|{index}"
            )
            + ".npy"
        )
        for index in range(CALIBRATION_WINDOWS_PER_EVENT)
    ]


def _calibration_anchor_metadata_path(
    row: dict[str, Any], backend_name: str, vault_root: Path
) -> Path:
    key = SMR.sha256_text(
        str(row.get("audio_sha256") or "")
        + "|" + backend_name + "|candidate-domain-calibration-metadata-v2"
    )
    return vault_root / backend_name / (key + ".json")


def build_embedding_vault(
    rows: Sequence[dict[str, Any]], backend: Any, vault_root: Path,
    *, max_anchors: int = 0, shard_index: int = 0, shard_count: int = 1,
    workers: int = 1, backend_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    if not getattr(backend, "for_production", False):
        raise IdentityRefused("test-only embedding backend cannot build a production vault")
    backend_name = str(backend.name)
    event_ids = [str(row.get("event_id") or "").strip() for row in rows]
    audio_hashes = [str(row.get("audio_sha256") or "").strip() for row in rows]
    if (
        any(not value for value in event_ids)
        or len(set(event_ids)) != len(event_ids)
        or any(not value for value in audio_hashes)
        or len(set(audio_hashes)) != len(audio_hashes)
    ):
        raise IdentityRefused("anchor event IDs and audio hashes must be nonblank and globally unique")
    if shard_count < 1 or shard_index < 0 or shard_index >= shard_count:
        raise IdentityRefused("embedding shard index/count is invalid")
    pending: list[tuple[int, dict[str, Any]]] = []
    for position, row in enumerate(rows):
        targets = [
            _primary_anchor_cache_path(row, backend_name, vault_root),
            *_calibration_anchor_cache_paths(row, backend_name, vault_root),
            _calibration_anchor_metadata_path(row, backend_name, vault_root),
        ]
        if all(path.exists() for path in targets):
            continue
        if position % shard_count == shard_index:
            pending.append((position, row))
    if max_anchors:
        pending = pending[:max_anchors]

    thread_state = threading.local()

    def worker_backend() -> Any:
        value = getattr(thread_state, "backend", None)
        if value is None:
            value = backend_factory() if backend_factory is not None else backend
            thread_state.backend = value
        return value

    def embed_event(item: tuple[int, dict[str, Any]]) -> int:
        _, row = item
        path = Path(row["path"])
        if row.get("audio_sha256") != SMR.sha256_file(path):
            raise IdentityRefused(f"anchor changed after manifest creation: {path}")
        samples, rate = _load_audio(path)
        model = worker_backend()
        written = 0
        primary_target = _primary_anchor_cache_path(row, backend_name, vault_root)
        if not primary_target.exists():
            window, _, _ = select_reference_window(samples, rate)
            vector = np.asarray(model.embed(window, rate), dtype=np.float32).reshape(-1)
            if vector.size == 0 or not np.all(np.isfinite(vector)):
                raise IdentityRefused(f"backend emitted invalid anchor vector: {path}")
            _save_npy_atomic(primary_target, vector)
            written += 1
        calibration_windows, reference_start_sec, reference_end_sec = select_calibration_windows(
            samples, rate
        )
        calibration_targets = _calibration_anchor_cache_paths(
            row, backend_name, vault_root
        )
        for target, window in zip(
            calibration_targets, calibration_windows,
        ):
            if target.exists():
                continue
            vector = np.asarray(model.embed(window, rate), dtype=np.float32).reshape(-1)
            if vector.size == 0 or not np.all(np.isfinite(vector)):
                raise IdentityRefused(f"backend emitted invalid calibration vector: {path}")
            _save_npy_atomic(target, vector)
            written += 1
        width = int(round(CALIBRATION_WINDOW_SEC * rate))
        reference_start_frame = int(round(reference_start_sec * rate))
        metadata = {
            "schema": "speech-speaker-anchor-calibration-event-v2",
            "anchor_id": row["anchor_id"],
            "member": row["member"],
            "event_id": row["event_id"],
            "source_audio_sha256": row["audio_sha256"],
            "backend": backend_name,
            "protocol": "held-out-event-candidate-domain-unanimous-best-anchor-v2",
            "sample_rate": rate,
            "reference_start_frame": reference_start_frame,
            "reference_end_frame": int(round(reference_end_sec * rate)),
            "windows": [
                {
                    "window_index": index,
                    "source_start_frame": reference_start_frame + index * width,
                    "source_frame_count": width,
                    "embedding": str(target.resolve()),
                    "embedding_sha256": SMR.sha256_file(target),
                }
                for index, target in enumerate(calibration_targets)
            ],
        }
        metadata["event_row_sha256"] = _row_hash(metadata, "event_row_sha256")
        SMR.write_json_atomic(
            _calibration_anchor_metadata_path(row, backend_name, vault_root), metadata
        )
        return written

    processed = 0
    embeddings_written = 0
    worker_count = max(1, int(workers))
    if worker_count == 1:
        for item in pending:
            embeddings_written += embed_event(item)
            processed += 1
            if processed % 10 == 0 or processed == len(pending):
                print(f"ANCHOR EVENTS processed={processed}/{len(pending)}", flush=True)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(embed_event, item): item for item in pending}
            for future in concurrent.futures.as_completed(futures):
                embeddings_written += future.result()
                processed += 1
                if processed % 10 == 0 or processed == len(pending):
                    print(f"ANCHOR EVENTS processed={processed}/{len(pending)}", flush=True)

    available: list[tuple[dict[str, Any], np.ndarray]] = []
    calibration_available: list[tuple[dict[str, Any], int, np.ndarray]] = []
    calibration_manifest_rows: list[dict[str, Any]] = []
    for row in rows:
        target = _primary_anchor_cache_path(row, backend_name, vault_root)
        if target.exists():
            vector = np.load(target, allow_pickle=False).astype(np.float32)
            if vector.ndim != 1 or not np.all(np.isfinite(vector)):
                raise IdentityRefused(f"invalid cached anchor embedding: {target}")
            available.append((row, vector))
        for index, calibration_target in enumerate(
            _calibration_anchor_cache_paths(row, backend_name, vault_root)
        ):
            if calibration_target.exists():
                vector = np.load(calibration_target, allow_pickle=False).astype(np.float32)
                if vector.ndim != 1 or not np.all(np.isfinite(vector)):
                    raise IdentityRefused(
                        f"invalid cached calibration embedding: {calibration_target}"
                    )
                calibration_available.append((row, index, vector))
        metadata_path = _calibration_anchor_metadata_path(row, backend_name, vault_root)
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            if (
                metadata.get("schema") != "speech-speaker-anchor-calibration-event-v2"
                or metadata.get("event_row_sha256") != _row_hash(metadata, "event_row_sha256")
                or metadata.get("anchor_id") != row.get("anchor_id")
                or metadata.get("event_id") != row.get("event_id")
                or metadata.get("source_audio_sha256") != row.get("audio_sha256")
                or len(metadata.get("windows") or []) != CALIBRATION_WINDOWS_PER_EVENT
            ):
                raise IdentityRefused(f"invalid calibration event metadata: {metadata_path}")
            for window in metadata["windows"]:
                embedding = Path(str(window.get("embedding") or ""))
                manifest_row = {
                    "schema": "speech-speaker-anchor-calibration-window-v2",
                    "anchor_id": row["anchor_id"],
                    "member": row["member"],
                    "event_id": row["event_id"],
                    "source_audio_sha256": row["audio_sha256"],
                    "backend": backend_name,
                    "protocol": metadata["protocol"],
                    "preprocessing_id": "mono-16khz-pcm16/highest-rms-24s/3x8s-disjoint-v2",
                    "sample_rate": metadata["sample_rate"],
                    "window_index": int(window["window_index"]),
                    "source_start_frame": int(window["source_start_frame"]),
                    "source_frame_count": int(window["source_frame_count"]),
                    "embedding": str(embedding.resolve()),
                    "embedding_sha256": str(window["embedding_sha256"]),
                }
                if (
                    not embedding.is_file()
                    or SMR.sha256_file(embedding) != manifest_row["embedding_sha256"]
                    or manifest_row["sample_rate"] != 16000
                    or manifest_row["source_frame_count"]
                    != int(round(CALIBRATION_WINDOW_SEC * 16000))
                    or manifest_row["window_index"]
                    not in range(CALIBRATION_WINDOWS_PER_EVENT)
                ):
                    raise IdentityRefused(f"calibration embedding hash mismatch: {embedding}")
                manifest_row["window_row_sha256"] = _row_hash(
                    manifest_row, "window_row_sha256"
                )
                calibration_manifest_rows.append(manifest_row)
    calibration_total = len(rows) * CALIBRATION_WINDOWS_PER_EVENT
    status = (
        "PASS"
        if (
            len(available) == len(rows)
            and len(calibration_available) == calibration_total
            and len(calibration_manifest_rows) == calibration_total
        )
        else "IN_PROGRESS"
    )
    combined = vault_root / f"{backend_name}_anchor_vectors_v2.npz"
    calibration_manifest = vault_root / f"{backend_name}_anchor_calibration_windows_v2.jsonl"
    if status == "PASS":
        dims = {
            vector.shape for _, vector in available
        } | {
            vector.shape for _, _, vector in calibration_available
        }
        if len(dims) != 1:
            raise IdentityRefused("anchor embeddings have inconsistent dimensions")
        combined.parent.mkdir(parents=True, exist_ok=True)
        fd, raw = tempfile.mkstemp(prefix=combined.name + ".", suffix=".tmp", dir=combined.parent)
        os.close(fd)
        temp = Path(raw)
        try:
            with temp.open("wb") as handle:
                np.savez_compressed(
                    handle,
                    anchor_ids=np.asarray([row["anchor_id"] for row, _ in available]),
                    members=np.asarray([row["member"] for row, _ in available]),
                    event_ids=np.asarray([row["event_id"] for row, _ in available]),
                    audio_sha256=np.asarray([row["audio_sha256"] for row, _ in available]),
                    vectors=np.stack([vector for _, vector in available]).astype(np.float32),
                    calibration_anchor_ids=np.asarray([
                        f"{row['anchor_id']}:window:{index}"
                        for row, index, _ in calibration_available
                    ]),
                    calibration_members=np.asarray([
                        row["member"] for row, _, _ in calibration_available
                    ]),
                    calibration_event_ids=np.asarray([
                        row["event_id"] for row, _, _ in calibration_available
                    ]),
                    calibration_window_indexes=np.asarray([
                        index for _, index, _ in calibration_available
                    ], dtype=np.int16),
                    calibration_vectors=np.stack([
                        vector for _, _, vector in calibration_available
                    ]).astype(np.float32),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, combined)
        finally:
            if temp.exists():
                temp.unlink()
        SMR.write_jsonl_atomic(
            calibration_manifest,
            sorted(
                calibration_manifest_rows,
                key=lambda row: (str(row["event_id"]), int(row["window_index"])),
            ),
        )
    return {
        "status": status,
        "backend": backend_name,
        "anchors_total": len(rows),
        "anchors_embedded": len(available),
        "calibration_windows_total": calibration_total,
        "calibration_windows_embedded": len(calibration_available),
        "calibration_windows_per_event": CALIBRATION_WINDOWS_PER_EVENT,
        "calibration_window_sec": CALIBRATION_WINDOW_SEC,
        "calibration_protocol": "held-out-event-candidate-domain-unanimous-best-anchor-v2",
        "calibration_window_manifest": (
            str(calibration_manifest.resolve()) if status == "PASS" else None
        ),
        "calibration_window_manifest_sha256": (
            SMR.sha256_file(calibration_manifest) if status == "PASS" else None
        ),
        "processed_this_run": processed,
        "embeddings_written_this_run": embeddings_written,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "combined_vectors": str(combined.resolve()) if status == "PASS" else None,
        "combined_vectors_sha256": SMR.sha256_file(combined) if status == "PASS" else None,
    }


def _member_indexes(members: np.ndarray) -> dict[str, np.ndarray]:
    return {
        member: np.flatnonzero(members == member)
        for member in sorted(set(str(value) for value in members.tolist()))
    }


def calibrate_zero_false_accept(
    vectors: np.ndarray,
    members: Sequence[str],
    eligible_targets: set[str],
    *,
    event_ids: Sequence[str],
    calibration_vectors: np.ndarray,
    calibration_members: Sequence[str],
    calibration_event_ids: Sequence[str],
    calibration_window_indexes: Sequence[int],
) -> dict[str, Any]:
    """Calibrate the complete unanimous decision rule with held-out events.

    Each verified event supplies three candidate-duration windows.  That
    event's production anchor is excluded while its windows are tested against
    every vaulted speaker.  Thresholds are selected over whole sequences,
    because unanimity—not a single window—is the production acceptance unit.
    """
    vectors = np.asarray(vectors, dtype=np.float64)
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    member_array = np.asarray(list(members))
    event_array = np.asarray(list(event_ids)).astype(str)
    calibration_vectors = np.asarray(calibration_vectors, dtype=np.float64)
    calibration_vectors /= np.maximum(
        np.linalg.norm(calibration_vectors, axis=1, keepdims=True), 1e-12
    )
    calibration_member_array = np.asarray(list(calibration_members)).astype(str)
    calibration_event_array = np.asarray(list(calibration_event_ids)).astype(str)
    calibration_window_array = np.asarray(list(calibration_window_indexes), dtype=int)
    if (
        vectors.ndim != 2
        or vectors.shape[0] != member_array.size
        or event_array.size != member_array.size
        or calibration_vectors.ndim != 2
        or calibration_vectors.shape[0] != calibration_member_array.size
        or calibration_event_array.size != calibration_member_array.size
        or calibration_window_array.size != calibration_member_array.size
        or calibration_vectors.shape[1] != vectors.shape[1]
    ):
        raise IdentityRefused("calibration vector/member shape mismatch")
    if (
        any(not value.strip() for value in event_array.tolist())
        or any(not value.strip() for value in calibration_event_array.tolist())
        or set(calibration_event_array.tolist()) != set(event_array.tolist())
    ):
        raise IdentityRefused("calibration requires matching nonblank primary event IDs")
    primary_members_by_event = {
        event: set(member_array[event_array == event].tolist())
        for event in set(event_array.tolist())
    }
    if any(len(values) != 1 for values in primary_members_by_event.values()):
        raise IdentityRefused("primary anchor event is bound to multiple speakers")
    genuine: list[tuple[float, float, bool]] = []
    false_trials: list[tuple[float, float, bool]] = []
    genuine_by_claim: dict[str, list[tuple[float, float, bool]]] = {
        member: [] for member in sorted(eligible_targets)
    }
    false_by_claim: dict[str, list[tuple[float, float, bool]]] = {
        member: [] for member in sorted(eligible_targets)
    }
    remaining_target_references: list[int] = []
    skipped_no_remaining_true_reference = 0
    evaluated_events = 0
    held_out_events = sorted(set(calibration_event_array.tolist()))
    primary_member_by_event = {
        str(event): str(next(iter(values)))
        for event, values in primary_members_by_event.items()
    }
    for held_out_event in held_out_events:
        window_indexes = np.flatnonzero(calibration_event_array == held_out_event)
        if (
            window_indexes.size != CALIBRATION_WINDOWS_PER_EVENT
            or sorted(calibration_window_array[window_indexes].tolist())
            != list(range(CALIBRATION_WINDOWS_PER_EVENT))
        ):
            raise IdentityRefused("calibration event windows are incomplete or noncanonical")
        true_members = set(calibration_member_array[window_indexes].tolist())
        if len(true_members) != 1:
            raise IdentityRefused("calibration event contains multiple claimed speakers")
        true_member = next(iter(true_members))
        if primary_member_by_event.get(held_out_event) != true_member:
            raise IdentityRefused("calibration event/member binding differs from the primary anchor")
        training_indexes = np.flatnonzero(event_array != held_out_event)
        training_members = member_array[training_indexes]
        indexes = _member_indexes(training_members)
        if len(indexes) < 2:
            continue
        similarity = calibration_vectors[window_indexes] @ vectors[training_indexes].T
        best_by_member = {
            member: np.max(similarity[:, idx], axis=1)
            for member, idx in indexes.items()
        }
        scoring_members = sorted(best_by_member)
        scoring_member_index = {
            member: index for index, member in enumerate(scoring_members)
        }
        best_matrix = np.stack(
            [best_by_member[member] for member in scoring_members], axis=1
        )
        ranking = np.argsort(best_matrix, axis=1)
        top_indexes = ranking[:, -1]
        second_indexes = ranking[:, -2]
        top_scores = best_matrix[np.arange(best_matrix.shape[0]), top_indexes]
        second_scores = best_matrix[np.arange(best_matrix.shape[0]), second_indexes]
        true_reference_count = int(np.sum(training_members == true_member))
        if true_reference_count < 1:
            # The held-out recording cannot supply a production-realistic
            # genuine trial when its speaker has no other event.  It remains
            # an impostor event for every eligible claimed speaker, however;
            # omitting those wrong claims would weaken the zero-FAR stop bar.
            skipped_no_remaining_true_reference += 1
        else:
            evaluated_events += 1
            if true_member in eligible_targets:
                remaining_target_references.append(true_reference_count)
        for claimed in sorted(eligible_targets):
            target_scores = best_by_member.get(claimed)
            if target_scores is None:
                continue
            claimed_index = scoring_member_index[claimed]
            competitor_scores = np.where(
                top_indexes == claimed_index, second_scores, top_scores
            )
            score = float(np.min(target_scores))
            margin = float(np.min(target_scores - competitor_scores))
            unanimous = bool(np.all(target_scores > competitor_scores))
            trial = (score, margin, unanimous)
            if claimed == true_member:
                # A genuine sequence is defined only when at least one
                # different-event reference for the true speaker remains.
                if true_reference_count >= 1:
                    genuine.append(trial)
                    genuine_by_claim[claimed].append(trial)
            else:
                false_trials.append(trial)
                false_by_claim[claimed].append(trial)
    if not genuine or not false_trials:
        raise IdentityRefused("calibration lacks genuine or exhaustive wrong-claim sequences")
    false_unanimous = [trial for trial in false_trials if trial[2]]
    maximum_false_margin = max((margin for _, margin, _ in false_unanimous), default=0.0)
    raw_margin_floor = max(0.0, maximum_false_margin) + 1e-6
    margin_floor = math.ceil(raw_margin_floor * 1_000_000_000) / 1_000_000_000
    passing_genuine = [
        (score, margin) for score, margin, unanimous in genuine
        if unanimous and margin >= margin_floor
    ]
    raw_score_floor = min((score for score, _ in passing_genuine), default=1.0) - 1e-9
    score_floor = math.floor(raw_score_floor * 1_000_000_000) / 1_000_000_000
    false_accepts = sum(
        unanimous and score >= score_floor and margin >= margin_floor
        for score, margin, unanimous in false_trials
    )
    genuine_accepts = sum(
        unanimous and score >= score_floor and margin >= margin_floor
        for score, margin, unanimous in genuine
    )
    status = (
        "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
        if false_accepts == 0 and genuine_accepts > 0
        else "CALIBRATION_STOP_BAR_MISS"
    )
    per_claim_thresholds: dict[str, dict[str, Any]] = {}
    for claimed in sorted(eligible_targets):
        claim_genuine = genuine_by_claim[claimed]
        claim_false = false_by_claim[claimed]
        claim_false_unanimous = [trial for trial in claim_false if trial[2]]
        claim_maximum_false_margin = max(
            (margin for _, margin, _ in claim_false_unanimous), default=0.0
        )
        claim_margin_floor = math.ceil(
            (max(0.0, claim_maximum_false_margin) + 1e-6) * 1_000_000_000
        ) / 1_000_000_000
        claim_passing_genuine = [
            (score, margin) for score, margin, unanimous in claim_genuine
            if unanimous and margin >= claim_margin_floor
        ]
        # Margin is the exhaustive wrong-claim stop bar.  A claim with no
        # different-event genuine trial can still be evaluated against every
        # impostor event; its accepted reference established the label, while
        # this zero-FAR calibration establishes the rejection boundary.
        claim_score_floor = math.floor(
            (min((score for score, _ in claim_passing_genuine), default=0.0) - 1e-9)
            * 1_000_000_000
        ) / 1_000_000_000
        claim_false_accepts = sum(
            unanimous and score >= claim_score_floor and margin >= claim_margin_floor
            for score, margin, unanimous in claim_false
        )
        claim_genuine_accepts = sum(
            unanimous and score >= claim_score_floor and margin >= claim_margin_floor
            for score, margin, unanimous in claim_genuine
        )
        per_claim_thresholds[claimed] = {
            "minimum_target_best_score": claim_score_floor,
            "minimum_best_anchor_margin": claim_margin_floor,
            "wrong_claim_sequences": len(claim_false),
            "false_unanimous_sequences": len(claim_false_unanimous),
            "false_accepts": claim_false_accepts,
            "genuine_sequences": len(claim_genuine),
            "genuine_accepts": claim_genuine_accepts,
            "held_out_genuine_available": bool(claim_genuine),
        }
    per_claim_false_accepts = sum(
        int(row["false_accepts"]) for row in per_claim_thresholds.values()
    )
    per_claim_genuine_accepts = sum(
        int(row["genuine_accepts"]) for row in per_claim_thresholds.values()
    )
    return {
        "schema": CALIBRATION_SCHEMA,
        "status": status,
        "operational_confidence_label": "100_PERCENT_STRUCTURAL_CONSENSUS",
        "probability_claim": False,
        "minimum_target_best_score": score_floor,
        "minimum_best_anchor_margin": margin_floor,
        "genuine_sequences": len(genuine),
        "genuine_accepts": genuine_accepts,
        "genuine_accept_rate": genuine_accepts / len(genuine),
        "wrong_claim_sequences": len(false_trials),
        "false_unanimous_sequences": len(false_unanimous),
        "false_accepts": false_accepts,
        "empirical_precision": 1.0 if false_accepts == 0 else None,
        "all_vaulted_speakers_compared": True,
        "held_out_events": len(held_out_events),
        "held_out_events_evaluated": evaluated_events,
        "events_skipped_no_remaining_true_reference": skipped_no_remaining_true_reference,
        "event_holdout_enforced": True,
        "primary_event_ids_unique": len(set(event_array.tolist())) == event_array.size,
        "same_event_anchor_windows_excluded_together": True,
        "minimum_remaining_target_references": min(remaining_target_references),
        "candidate_domain_calibration_windows": int(calibration_vectors.shape[0]),
        "minimum_unanimous_windows": MIN_CANDIDATE_WINDOWS,
        "target_score": "best cross-event anchor cosine across every vaulted target anchor",
        "competitor_score": "best anchor cosine for every other vaulted speaker",
        "calibration_unit": "held-out-event three-window unanimous sequence",
        "scoring_contract": identity_scoring_contract(),
        "scoring_contract_sha256": identity_scoring_contract_sha256(),
        "serialized_thresholds_rechecked": True,
        "per_claim_thresholds": per_claim_thresholds,
        "per_claim_thresholds_status": (
            "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
            if per_claim_false_accepts == 0 and per_claim_genuine_accepts > 0
            else "CALIBRATION_STOP_BAR_MISS"
        ),
        "per_claim_false_accepts": per_claim_false_accepts,
        "per_claim_genuine_accepts": per_claim_genuine_accepts,
    }


def evaluate_identity(
    candidate_vectors: np.ndarray,
    claimed_member: str,
    anchor_vectors: np.ndarray,
    anchor_members: Sequence[str],
    eligible_targets: set[str],
    calibration: dict[str, Any],
    anchor_ids: Sequence[str] | None = None,
    anchor_event_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Evaluate one claimed speaker; any dissent leaves it unresolved."""
    candidate_vectors = np.asarray(candidate_vectors, dtype=np.float64)
    anchor_vectors = np.asarray(anchor_vectors, dtype=np.float64)
    member_array = np.asarray(list(anchor_members))
    anchor_id_array = np.asarray(
        list(anchor_ids) if anchor_ids is not None else [f"anchor:{i}" for i in range(len(member_array))]
    ).astype(str)
    anchor_event_array = np.asarray(
        list(anchor_event_ids)
        if anchor_event_ids is not None
        else [f"event:{i}" for i in range(len(member_array))]
    ).astype(str)
    vaulted = sorted(set(str(value) for value in member_array.tolist()))
    base = {
        "schema": IDENTITY_SCHEMA,
        "claimed_member": claimed_member,
        "operational_confidence_label": "100_PERCENT_STRUCTURAL_CONSENSUS",
        "probability_claim": False,
        "vaulted_speakers_compared": len(vaulted),
        "all_vaulted_speakers_compared": True,
    }
    reasons: list[str] = []
    if (
        calibration.get("status") != "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
        or calibration.get("scoring_contract_sha256") != identity_scoring_contract_sha256()
    ):
        reasons.append("CALIBRATION_NOT_PASS")
    if claimed_member not in eligible_targets:
        reasons.append("TARGET_ANCHOR_FLOOR_NOT_MET")
    if candidate_vectors.ndim != 2 or candidate_vectors.shape[0] < MIN_CANDIDATE_WINDOWS:
        reasons.append("TOO_FEW_CANDIDATE_WINDOWS")
    if (
        anchor_vectors.ndim != 2
        or anchor_vectors.shape[0] != member_array.size
        or anchor_id_array.size != member_array.size
        or anchor_event_array.size != member_array.size
    ):
        reasons.append("ANCHOR_MATRIX_INVALID")
    if reasons:
        return {**base, "status": "UNRESOLVED", "reasons": reasons, "windows": []}
    candidate_vectors /= np.maximum(np.linalg.norm(candidate_vectors, axis=1, keepdims=True), 1e-12)
    anchor_vectors /= np.maximum(np.linalg.norm(anchor_vectors, axis=1, keepdims=True), 1e-12)
    indexes = _member_indexes(member_array)
    target_indexes = indexes.get(claimed_member, np.asarray([], dtype=int))
    per_claim = calibration.get("per_claim_thresholds") or {}
    claim_thresholds = per_claim.get(claimed_member)
    if (
        calibration.get("per_claim_thresholds_status")
        != "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
        or not isinstance(claim_thresholds, dict)
        or int(claim_thresholds.get("false_accepts", -1)) != 0
    ):
        reasons.append("PER_CLAIM_ZERO_FALSE_ACCEPT_THRESHOLD_MISSING")
        return {**base, "status": "UNRESOLVED", "reasons": reasons, "windows": []}
    minimum_score = float(claim_thresholds["minimum_target_best_score"])
    minimum_margin = float(claim_thresholds["minimum_best_anchor_margin"])
    windows: list[dict[str, Any]] = []
    similarity = candidate_vectors @ anchor_vectors.T
    for window_index, scores in enumerate(similarity):
        target_scores = np.sort(scores[target_indexes])[::-1]
        if target_scores.size < MIN_TARGET_ANCHORS:
            reasons.append(f"WINDOW_{window_index}_TARGET_ANCHORS_MISSING")
            continue
        target_best = float(target_scores[0])
        target_anchor_index = int(target_indexes[np.argmax(scores[target_indexes])])
        ranking = []
        for member, idx in indexes.items():
            member_anchor_index = int(idx[np.argmax(scores[idx])])
            ranking.append((float(scores[member_anchor_index]), member, member_anchor_index))
        ranking.sort(reverse=True)
        competitor_best, competitor_member, competitor_anchor_index = max(
            (score, member, anchor_index)
            for score, member, anchor_index in ranking
            if member != claimed_member
        )
        margin = target_best - competitor_best
        winner_score, winner, winner_anchor_index = ranking[0]
        passed = (
            winner == claimed_member
            and target_best > competitor_best
            and target_best >= minimum_score
            and margin >= minimum_margin
        )
        if not passed:
            reasons.append(f"WINDOW_{window_index}_CONSENSUS_FAIL")
        windows.append({
            "window_index": window_index,
            "winner": winner,
            "target_best": round(target_best, 9),
            "target_best_anchor_id": anchor_id_array[target_anchor_index],
            "target_best_anchor_event_id": anchor_event_array[target_anchor_index],
            "runner_up": competitor_member,
            "runner_up_best": round(float(competitor_best), 9),
            "runner_up_anchor_id": anchor_id_array[competitor_anchor_index],
            "runner_up_anchor_event_id": anchor_event_array[competitor_anchor_index],
            "winner_anchor_id": anchor_id_array[winner_anchor_index],
            "winner_anchor_event_id": anchor_event_array[winner_anchor_index],
            "best_anchor_margin": round(float(margin), 9),
            "passed": passed,
            "top_five": [
                {
                    "member": member,
                    "best_anchor_score": round(score, 9),
                    "anchor_id": anchor_id_array[anchor_index],
                    "anchor_event_id": anchor_event_array[anchor_index],
                }
                for score, member, anchor_index in ranking[:5]
            ],
        })
    accepted = len(windows) == candidate_vectors.shape[0] and not reasons and all(
        row["passed"] for row in windows
    )
    accepted_runs: list[dict[str, Any]] = []
    run_start: int | None = None
    for index in range(len(windows) + 1):
        passing = index < len(windows) and bool(windows[index]["passed"])
        if passing and run_start is None:
            run_start = index
        if not passing and run_start is not None:
            if index - run_start >= MIN_CANDIDATE_WINDOWS:
                run_windows = windows[run_start:index]
                accepted_runs.append({
                    "start_window": run_start,
                    "end_window_exclusive": index,
                    "window_count": index - run_start,
                    "minimum_target_best_score": min(
                        float(row["target_best"]) for row in run_windows
                    ),
                    "minimum_best_anchor_margin": min(
                        float(row["best_anchor_margin"]) for row in run_windows
                    ),
                    "all_windows_passed": True,
                })
            run_start = None
    status = (
        "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
        if accepted
        else "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
        if accepted_runs
        else "UNRESOLVED"
    )
    return {
        **base,
        "status": status,
        "reasons": reasons,
        "windows": windows,
        "accepted_runs": accepted_runs,
        "accepted_run_count": len(accepted_runs),
        "minimum_target_best_score": minimum_score,
        "minimum_best_anchor_margin": minimum_margin,
        "calibration_unit": calibration.get("calibration_unit"),
        "scoring_contract_sha256": identity_scoring_contract_sha256(),
        "unanimous_windows": accepted,
    }


def candidate_window_bounds(
    frames: int, sample_rate: int, window_sec: float = 8.0
) -> list[tuple[int, int]]:
    if frames <= 0 or sample_rate <= 0:
        return []
    width = int(round(window_sec * sample_rate))
    if width <= 0 or frames < width:
        return []
    # Calibration uses exact, disjoint 8 s windows.  Runtime evidence must
    # stay in that domain: a shorter tail is never embedded, merged, or cut.
    return [(start, start + width) for start in range(0, frames - width + 1, width)]


def candidate_identity_coverage(
    bounds: Sequence[tuple[int, int]],
    total_frames: int,
    sample_rate: int,
    window_sec: float = CALIBRATION_WINDOW_SEC,
) -> dict[str, Any]:
    width = int(round(float(window_sec) * int(sample_rate)))
    exact_bounds = bool(bounds) and width > 0 and all(
        int(start) == index * width and int(end) == (index + 1) * width
        for index, (start, end) in enumerate(bounds)
    )
    tested_frames = int(bounds[-1][1]) if bounds else 0
    excluded_tail_frames = int(total_frames) - tested_frames
    if excluded_tail_frames < 0:
        raise IdentityRefused("identity windows extend beyond the candidate PCM")
    return {
        "candidate_total_frames": int(total_frames),
        "identity_tested_frame_count": tested_frames,
        "identity_excluded_tail_frames": excluded_tail_frames,
        "full_candidate_identity_coverage": bool(
            exact_bounds and tested_frames == int(total_frames)
        ),
    }


def whole_candidate_identity_coverage_valid(
    decision: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    try:
        candidate_frames = int(candidate.get("source_frame_count"))
        exact_width = int(round(CALIBRATION_WINDOW_SEC * 16000))
        return (
            candidate_frames >= MIN_CANDIDATE_WINDOWS * exact_width
            and candidate_frames % exact_width == 0
            and decision.get("full_candidate_identity_coverage") is True
            and int(decision.get("candidate_total_frames")) == candidate_frames
            and int(decision.get("identity_tested_frame_count")) == candidate_frames
            and int(decision.get("identity_excluded_tail_frames")) == 0
        )
    except (TypeError, ValueError):
        return False


def retention_ready(
    transcript_receipt: dict[str, Any], candidates: Sequence[dict[str, Any]],
    decisions_by_clip: dict[str, dict[str, Any]], final_by_clip: dict[str, dict[str, Any]],
    *, final_root: Path = DEFAULT_FINAL_ROOT, minimum_ratio: float = 0.68,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if transcript_receipt.get("schema") != SMR.ASR_RECEIPT_SCHEMA or transcript_receipt.get("status") != "PASS":
        reasons.append("TRANSCRIPT_RECEIPT_NOT_PASS")
    if not str(transcript_receipt.get("reference_url") or ""):
        reasons.append("REFERENCE_URL_MISSING")
    if not candidates:
        reasons.append("NO_ALIGNED_CANDIDATES")
    expected_turns = {
        int(rowid)
        for candidate in candidates
        for rowid in (candidate.get("source_expected_turn_rowids") or [])
    }
    actual_turns = set()
    for candidate in candidates:
        match = re.fullmatch(r"sqlite:speeches:(\d+)", str(candidate.get("turn_id") or ""))
        if match:
            actual_turns.add(int(match.group(1)))
    if not expected_turns or expected_turns != actual_turns:
        reasons.append("SOURCE_TURN_COVERAGE_INCOMPLETE")
    for candidate in candidates:
        clip_id = str(candidate.get("clip_id") or "")
        decision = decisions_by_clip.get(clip_id)
        final = final_by_clip.get(clip_id)
        if candidate.get("clip_sha256") != SMR._candidate_hash(candidate):
            reasons.append(f"CANDIDATE_HASH_INVALID:{clip_id}")
        try:
            _validate_transcript_binding(candidate)
            _validate_candidate_cut_binding(candidate, minimum_ratio=minimum_ratio)
        except (IdentityRefused, OSError, ValueError, json.JSONDecodeError) as exc:
            reasons.append(f"CANDIDATE_BINDING_INVALID:{clip_id}:{exc}")
        if (
            not decision
            or decision.get("schema") != IDENTITY_SCHEMA
            or decision.get("status") != "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
            or decision.get("scoring_contract_sha256")
            != identity_scoring_contract_sha256()
        ):
            reasons.append(f"IDENTITY_NOT_ACCEPTED:{clip_id}")
        elif (
            decision.get("identity_row_sha256") != _row_hash(decision, "identity_row_sha256")
            or decision.get("candidate_clip_sha256") != candidate.get("clip_sha256")
            or decision.get("candidate_audio_sha256") != candidate.get("audio_sha256")
            or decision.get("candidate_cut_binding_sha256") != candidate.get("cut_binding_sha256")
            or not whole_candidate_identity_coverage_valid(decision, candidate)
        ):
            reasons.append(f"IDENTITY_BINDING_INVALID:{clip_id}")
        else:
            comparison = Path(str(decision.get("comparison_matrix") or ""))
            if (
                not comparison.is_file()
                or SMR.sha256_file(comparison) != decision.get("comparison_matrix_sha256")
            ):
                reasons.append(f"IDENTITY_COMPARISON_INVALID:{clip_id}")
        if not final or final.get("schema") != FINAL_SCHEMA or final.get("status") != "VERIFIED_FINAL":
            reasons.append(f"FINAL_CUT_NOT_SECURED:{clip_id}")
        elif final.get("final_row_sha256") != _row_hash(final, "final_row_sha256"):
            reasons.append(f"FINAL_ROW_HASH_INVALID:{clip_id}")
        else:
            final_path = Path(str(final.get("wav") or ""))
            if not _within(final_path, final_root):
                reasons.append(f"FINAL_PATH_OUTSIDE_ROOT:{clip_id}")
            elif not final_path.is_file():
                reasons.append(f"FINAL_WAV_MISSING:{clip_id}")
            elif SMR.sha256_file(final_path) != final.get("audio_sha256"):
                reasons.append(f"FINAL_WAV_HASH_INVALID:{clip_id}")
            elif final.get("audio_sha256") != candidate.get("audio_sha256"):
                reasons.append(f"FINAL_NOT_IDENTITY_AUDIO:{clip_id}")
            if (
                final.get("identity_row_sha256") != (decision or {}).get("identity_row_sha256")
                or final.get("candidate_clip_sha256") != candidate.get("clip_sha256")
                or final.get("cut_binding_sha256") != candidate.get("cut_binding_sha256")
            ):
                reasons.append(f"FINAL_BINDING_INVALID:{clip_id}")
    return not reasons, reasons


def inventory_source_turn_requirements(
    inventory_rows: Sequence[dict[str, Any]],
) -> tuple[dict[str, set[int]], dict[str, set[str]]]:
    """Return inventory-wide required turns/packages for each physical source WAV."""
    expected_turns: dict[str, set[int]] = collections.defaultdict(set)
    source_packages: dict[str, set[str]] = collections.defaultdict(set)
    for record in inventory_rows:
        acquisition = record.get("acquisition") or {}
        package_turns = {
            int(rowid)
            for member in (record.get("members") or [])
            for rowid in (member.get("rowids") or [])
        }
        for item in acquisition.get("items") or []:
            source_value = str(item.get("wav") or "")
            if not source_value:
                continue
            source_key = str(Path(source_value).resolve())
            expected_turns[source_key].update(
                int(rowid) for rowid in (item.get("caption_rowids") or package_turns)
            )
            source_packages[source_key].add(str(record.get("package_id") or ""))
    return expected_turns, source_packages


def load_vault(vault_path: Path) -> dict[str, np.ndarray]:
    if not vault_path.is_file():
        raise IdentityRefused(f"combined anchor vault is missing: {vault_path}")
    with np.load(vault_path, allow_pickle=False) as value:
        required = {
            "anchor_ids", "members", "event_ids", "audio_sha256", "vectors",
            "calibration_anchor_ids", "calibration_members", "calibration_event_ids",
            "calibration_window_indexes", "calibration_vectors",
        }
        if set(value.files) != required:
            raise IdentityRefused("combined anchor vault schema mismatch")
        return {name: value[name] for name in required}


def _row_hash(row: dict[str, Any], field: str) -> str:
    body = dict(row)
    body.pop(field, None)
    return SMR.sha256_json(body)


def validate_merge_identity_decision(
        decision: dict[str, Any], candidate: dict[str, Any], calibration_sha256: str) -> str:
    """Validate the exact scored or deterministic compact identity-row shape.

    ``identify`` emits a deliberately compact UNRESOLVED row when an otherwise
    valid candidate contains fewer than three complete 8-second windows.  Such
    a row has no comparison matrix or candidate/calibration promotion binding
    and must remain permanently incapable of yielding a final.
    """
    clip_id = str(decision.get("clip_id") or "")
    if (
        decision.get("schema") != IDENTITY_SCHEMA
        or not clip_id
        or clip_id != str(candidate.get("clip_id") or "")
        or decision.get("identity_row_sha256")
        != _row_hash(decision, "identity_row_sha256")
    ):
        raise IdentityRefused("merge identity decision schema/hash/clip binding is stale")
    compact = "candidate_clip_sha256" not in decision
    if compact:
        try:
            identified_at = dt.datetime.fromisoformat(str(decision.get("identified_at") or ""))
            candidate_frames = int(candidate.get("source_frame_count", -1))
        except (TypeError, ValueError) as exc:
            raise IdentityRefused("compact unresolved identity timestamp is invalid") from exc
        if (
            frozenset(decision) != COMPACT_UNRESOLVED_FIELDS
            or decision.get("status") != "UNRESOLVED"
            or decision.get("reasons") != [COMPACT_UNRESOLVED_REASON]
            or decision.get("claimed_member") != candidate.get("member")
            or decision.get("operational_confidence_label")
            != "100_PERCENT_STRUCTURAL_CONSENSUS"
            or decision.get("probability_claim") is not False
            or decision.get("scoring_contract_sha256")
            != identity_scoring_contract_sha256()
            or identified_at.tzinfo is None
            or not (0 < candidate_frames < MIN_CANDIDATE_WINDOWS * 8 * 16000)
        ):
            raise IdentityRefused("compact unresolved identity row is malformed or promotable")
        return "compact_unresolved_short_window"

    comparison = Path(str(decision.get("comparison_matrix") or ""))
    if (
        decision.get("status") not in {
            "UNRESOLVED", "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
            "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
        }
        or decision.get("candidate_clip_sha256") != candidate.get("clip_sha256")
        or decision.get("candidate_audio_sha256") != candidate.get("audio_sha256")
        or decision.get("candidate_cut_binding_sha256") != candidate.get("cut_binding_sha256")
        or decision.get("calibration_sha256") != calibration_sha256
        or decision.get("scoring_contract_sha256") != identity_scoring_contract_sha256()
        or not comparison.is_absolute() or not comparison.is_file()
        or decision.get("comparison_matrix_sha256") != SMR.sha256_file(comparison)
    ):
        raise IdentityRefused("merge scored identity decision binding is stale")
    return "scored"


def identity_run_clip_id(parent_clip_id: str, run: dict[str, Any]) -> str:
    return (
        f"{parent_clip_id}:run:{int(run['start_window'])}-"
        f"{int(run['end_window_exclusive'])}"
    )


def identity_window_clip_id(
    parent_clip_id: str, run: dict[str, Any], window_index: int
) -> str:
    """Name one exact verified window without losing its qualifying-run lineage."""
    return f"{identity_run_clip_id(parent_clip_id, run)}:window:{int(window_index)}"


def validate_identity_run_window_binding(
    decision: dict[str, Any], run: dict[str, Any]
) -> None:
    """Prove a partial run names persisted, passing decision windows."""
    windows = decision.get("windows")
    try:
        start = int(run.get("start_window"))
        end = int(run.get("end_window_exclusive"))
        count = int(run.get("window_count"))
    except (TypeError, ValueError) as exc:
        raise IdentityRefused("identity run has invalid window indexes") from exc
    if (
        not isinstance(windows, list)
        or start < 0
        or end <= start
        or end > len(windows)
        or count != end - start
        or count < MIN_CANDIDATE_WINDOWS
    ):
        raise IdentityRefused(
            "identity run is outside the persisted decision windows"
        )
    claimed_member = str(decision.get("claimed_member") or "")
    for absolute_index, window in enumerate(windows[start:end], start=start):
        if (
            not isinstance(window, dict)
            or int(window.get("window_index", -1)) != absolute_index
            or window.get("passed") is not True
            or str(window.get("winner") or "") != claimed_member
        ):
            raise IdentityRefused(
                "identity run does not map exactly to passing claimed-speaker windows"
            )


def identity_window_lineage(
    candidate: dict[str, Any],
    decision: dict[str, Any],
    run: dict[str, Any],
    window_index: int,
) -> dict[str, Any]:
    """Bind one exact 8-second final unit to its accepted run and evidence row."""
    if (
        run.get("identity_run_sha256") != _row_hash(run, "identity_run_sha256")
        or run.get("parent_candidate_clip_sha256") != candidate.get("clip_sha256")
        or run.get("scoring_contract_sha256") != identity_scoring_contract_sha256()
        or run.get("all_windows_passed") is not True
    ):
        raise IdentityRefused("accepted run binding is stale")
    validate_identity_run_window_binding(decision, run)
    validate_identity_run_geometry(candidate, run)
    index = int(window_index)
    start_window = int(run["start_window"])
    end_window = int(run["end_window_exclusive"])
    windows = decision.get("windows") or []
    if not (start_window <= index < end_window < len(windows) + 1):
        raise IdentityRefused("identity window is outside its qualifying accepted run")
    window = windows[index]
    claimed_member = str(decision.get("claimed_member") or "")
    if (
        not isinstance(window, dict)
        or int(window.get("window_index", -1)) != index
        or window.get("passed") is not True
        or str(window.get("winner") or "") != claimed_member
    ):
        raise IdentityRefused("identity window is not an independently passing window")
    exact_width = int(round(CALIBRATION_WINDOW_SEC * 16000))
    relative_start = index * exact_width
    lineage = {
        "schema": "speech-source-speaker-identity-window-v1",
        "parent_candidate_clip_sha256": candidate.get("clip_sha256"),
        "identity_row_sha256": decision.get("identity_row_sha256"),
        "identity_run_sha256": run.get("identity_run_sha256"),
        "identity_window_index": index,
        "qualifying_run_start_window": start_window,
        "qualifying_run_end_window_exclusive": end_window,
        "relative_start_frame": relative_start,
        "source_start_frame": int(candidate.get("source_start_frame") or 0)
        + relative_start,
        "source_frame_count": exact_width,
        "sample_rate": 16000,
        "window_evidence_sha256": SMR.sha256_json(window),
        "scoring_contract_sha256": identity_scoring_contract_sha256(),
    }
    lineage["identity_window_sha256"] = _row_hash(
        lineage, "identity_window_sha256"
    )
    return lineage


def accepted_identity_window_units(
    decision: dict[str, Any], candidate: dict[str, Any]
) -> list[dict[str, Any]]:
    """Expand qualifying three-plus-window runs into exact per-window units.

    The sequence threshold remains three independently passing windows.  This
    expansion happens only after that gate has passed; it changes accounting
    and cutting granularity, not speaker-identity acceptance.
    """
    status = decision.get("status")
    if status not in {
        "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
        "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
    }:
        return []
    accepted_runs = decision.get("accepted_runs") or []
    if not accepted_runs:
        if status == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS":
            # Retain read compatibility for pre-run-lineage decisions.  Current
            # identification always persists accepted_runs and therefore takes
            # the exact-window path below.
            return []
        raise IdentityRefused("partial accepted identity has no accepted runs")
    if int(decision.get("accepted_run_count") or -1) != len(accepted_runs):
        raise IdentityRefused("accepted-run count does not match persisted runs")
    units: list[dict[str, Any]] = []
    seen_windows: set[int] = set()
    parent_clip_id = str(decision.get("clip_id") or "")
    for run in accepted_runs:
        start_window = int(run.get("start_window", -1))
        end_window = int(run.get("end_window_exclusive", -1))
        for window_index in range(start_window, end_window):
            if window_index in seen_windows:
                raise IdentityRefused("accepted runs overlap at an identity window")
            lineage = identity_window_lineage(
                candidate, decision, run, window_index
            )
            seen_windows.add(window_index)
            units.append({
                "clip_id": identity_window_clip_id(
                    parent_clip_id, run, window_index
                ),
                "run": run,
                "window_index": window_index,
                "lineage": lineage,
            })
    if status == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS":
        windows = decision.get("windows") or []
        if not whole_candidate_identity_coverage_valid(decision, candidate):
            raise IdentityRefused(
                "whole accepted identity does not cover every candidate frame"
            )
        if seen_windows != set(range(len(windows))):
            raise IdentityRefused(
                "whole accepted identity runs do not cover every persisted window"
            )
    if not units:
        raise IdentityRefused("accepted identity has no exact window units")
    return units


def expected_secure_final_ids(
    decisions: Sequence[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
    calibration_sha256: str,
    minimum_alignment_ratio: float = LOCKED_MINIMUM_ALIGNMENT_RATIO,
) -> tuple[set[str], list[dict[str, str]]]:
    """Derive every final unit implied by current accepted identity rows."""
    minimum_alignment_ratio = validate_locked_alignment_threshold(
        minimum_alignment_ratio
    )
    expected: set[str] = set()
    errors: list[dict[str, str]] = []

    def add_expected(final_clip_id: str, parent_clip_id: str) -> None:
        if final_clip_id in expected:
            errors.append({
                "clip_id": parent_clip_id,
                "reason": f"duplicate secure final identity: {final_clip_id}",
            })
        expected.add(final_clip_id)

    for decision in decisions:
        status = decision.get("status")
        if status not in {
            "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
            "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
        }:
            continue
        parent_clip_id = str(decision.get("clip_id") or "")
        candidate = candidates.get(parent_clip_id)
        try:
            alignment_valid = bool(
                candidate
                and math.isfinite(float(candidate.get("alignment_ratio")))
                and minimum_alignment_ratio
                <= float(candidate.get("alignment_ratio"))
                <= 1.0
            )
        except (TypeError, ValueError):
            alignment_valid = False
        common_valid = bool(
            alignment_valid
            and candidate.get("clip_sha256") == SMR._candidate_hash(candidate)
            and decision.get("schema") == IDENTITY_SCHEMA
            and decision.get("identity_row_sha256")
            == _row_hash(decision, "identity_row_sha256")
            and decision.get("candidate_clip_sha256") == candidate.get("clip_sha256")
            and decision.get("candidate_audio_sha256") == candidate.get("audio_sha256")
            and decision.get("candidate_cut_binding_sha256")
            == candidate.get("cut_binding_sha256")
            and decision.get("calibration_sha256") == calibration_sha256
            and decision.get("scoring_contract_sha256")
            == identity_scoring_contract_sha256()
        )
        if not common_valid:
            errors.append({
                "clip_id": parent_clip_id,
                "reason": "accepted identity unit has a stale candidate/calibration/scoring binding",
            })
            continue
        try:
            units = accepted_identity_window_units(decision, candidate)
        except (IdentityRefused, TypeError, ValueError) as exc:
            errors.append({
                "clip_id": parent_clip_id,
                "reason": "invalid accepted identity windows: " + str(exc),
            })
            continue
        if units:
            for unit in units:
                add_expected(str(unit["clip_id"]), parent_clip_id)
        elif status == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS":
            if not whole_candidate_identity_coverage_valid(decision, candidate):
                errors.append({
                    "clip_id": parent_clip_id,
                    "reason": "whole accepted identity does not cover every candidate frame",
                })
                continue
            add_expected(parent_clip_id, parent_clip_id)
    return expected, errors


def cumulative_verified_source_ledger(
    candidate_rows: Sequence[dict[str, Any]],
    decisions: Sequence[dict[str, Any]],
    final_rows: Sequence[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Bind every source's auditable candidates, decisions, and final units."""
    errors: list[dict[str, str]] = []
    candidates = [row for row in candidate_rows if row.get("schema") == SMR.CLIP_SCHEMA]
    candidate_by_id: dict[str, dict[str, Any]] = {}
    candidate_audio_sources: dict[str, str] = {}
    for row in candidates:
        clip_id = str(row.get("clip_id") or "")
        source = str(row.get("source_family") or "")
        audio = str(row.get("audio_sha256") or "")
        if not clip_id or clip_id in candidate_by_id or not source:
            errors.append({"clip_id": clip_id, "reason": "candidate IDs/sources are not unique"})
            continue
        candidate_by_id[clip_id] = row
        prior_source = candidate_audio_sources.get(audio)
        if audio and prior_source is not None and prior_source != source:
            errors.append({
                "clip_id": clip_id,
                "reason": "candidate audio identity is duplicated across source families",
            })
        if audio:
            candidate_audio_sources[audio] = source

    identity_by_id: dict[str, dict[str, Any]] = {}
    expected_by_source: dict[str, set[str]] = collections.defaultdict(set)
    for decision in decisions:
        parent = str(decision.get("clip_id") or "")
        candidate = candidate_by_id.get(parent)
        if not parent or parent in identity_by_id or candidate is None:
            errors.append({
                "clip_id": parent,
                "reason": "identity decision does not uniquely bind an auditable candidate",
            })
            continue
        identity_by_id[parent] = decision
        source = str(candidate["source_family"])
        if decision.get("status") in {
            "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
            "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
        }:
            try:
                units = accepted_identity_window_units(decision, candidate)
            except (IdentityRefused, TypeError, ValueError) as exc:
                errors.append({
                    "clip_id": parent,
                    "reason": "invalid accepted identity windows: " + str(exc),
                })
                units = []
            if units:
                expected_by_source[source].update(
                    str(unit["clip_id"]) for unit in units
                )
            elif decision.get("status") == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS":
                expected_by_source[source].add(parent)

    finals_by_id: dict[str, dict[str, Any]] = {}
    final_audio: set[str] = set()
    final_pcm: set[str] = set()
    final_runs: set[str] = set()
    final_windows: set[str] = set()
    frame_identities: set[tuple[str, int, int]] = set()
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
            errors.append({
                "clip_id": clip_id,
                "reason": "final does not uniquely preserve its candidate source binding",
            })
            continue
        if not audio or audio in final_audio:
            errors.append({"clip_id": clip_id, "reason": "duplicate final audio identity"})
        if identity_window and identity_window in final_windows:
            errors.append({"clip_id": clip_id, "reason": "duplicate identity-window lineage"})
        if run and not identity_window and run in final_runs:
            errors.append({"clip_id": clip_id, "reason": "duplicate accepted-run identity"})
        if pcm and pcm in final_pcm:
            errors.append({"clip_id": clip_id, "reason": "duplicate final PCM identity"})
        if frame_identity in frame_identities:
            errors.append({"clip_id": clip_id, "reason": "duplicate secured-source frame identity"})
        finals_by_id[clip_id] = row
        final_audio.add(audio)
        if pcm:
            final_pcm.add(pcm)
        frame_identities.add(frame_identity)
        if identity_window:
            final_windows.add(identity_window)
        elif run:
            final_runs.add(run)

    by_source: dict[str, dict[str, Any]] = {}
    sources = sorted(
        {str(row.get("source_family") or "") for row in candidates}
        | set(expected_by_source)
        | {str(row.get("source_family") or "") for row in final_rows}
    )
    for source in sources:
        if not source:
            errors.append({"clip_id": "", "reason": "empty source family in cumulative ledger"})
            continue
        source_candidates = [row for row in candidates if row.get("source_family") == source]
        source_identities = [
            row for row in decisions
            if str((candidate_by_id.get(str(row.get("clip_id") or "")) or {}).get(
                "source_family") or "") == source
        ]
        source_finals = [row for row in final_rows if row.get("source_family") == source]
        expected_ids = sorted(expected_by_source[source])
        final_ids = sorted(str(row["clip_id"]) for row in source_finals)
        by_source[source] = {
            "candidate_clips": len(source_candidates),
            "candidate_clip_ids_sha256": SMR.sha256_json(sorted(
                str(row["clip_id"]) for row in source_candidates)),
            "candidate_row_sha256s_sha256": SMR.sha256_json(sorted(
                str(row.get("clip_sha256") or "") for row in source_candidates)),
            "identity_decisions": len(source_identities),
            "identity_clip_ids_sha256": SMR.sha256_json(sorted(
                str(row["clip_id"]) for row in source_identities)),
            "identity_row_sha256s_sha256": SMR.sha256_json(sorted(
                str(row.get("identity_row_sha256") or "") for row in source_identities)),
            "expected_final_clips": len(expected_ids),
            "expected_final_ids_sha256": SMR.sha256_json(expected_ids),
            "verified_final_clips": len(source_finals),
            "final_ids_sha256": SMR.sha256_json(final_ids),
            "final_row_sha256s_sha256": SMR.sha256_json(sorted(
                str(row.get("final_row_sha256") or "") for row in source_finals)),
            "final_audio_sha256s_sha256": SMR.sha256_json(sorted(
                str(row.get("audio_sha256") or "") for row in source_finals)),
            "final_pcm_sha256s_sha256": SMR.sha256_json(sorted(
                str(row.get("source_slice_pcm_sha256") or "")
                for row in source_finals)),
            "identity_runs": sum(bool(row.get("identity_run_sha256")) for row in source_finals),
            "identity_run_sha256s_sha256": SMR.sha256_json(sorted(
                str(row.get("identity_run_sha256")) for row in source_finals
                if row.get("identity_run_sha256"))),
            "identity_windows": sum(
                bool(row.get("identity_window_sha256")) for row in source_finals
            ),
            "identity_window_sha256s_sha256": SMR.sha256_json(sorted(
                str(row.get("identity_window_sha256")) for row in source_finals
                if row.get("identity_window_sha256"))),
            "source_frame_bindings_sha256": SMR.sha256_json(sorted(
                "%s:%d:%d" % (
                    row.get("source_audio_sha256"), int(row.get("source_start_frame", -1)),
                    int(row.get("source_frame_count", -1)),
                ) for row in source_finals)),
            "final_id_set_complete": final_ids == expected_ids,
        }
    return by_source, errors


def _candidate_vectors(
    candidate: dict[str, Any], backend: Any, cache_root: Path, window_sec: float
) -> tuple[np.ndarray, list[tuple[int, int]], int]:
    if not math.isclose(
        float(window_sec), CALIBRATION_WINDOW_SEC, rel_tol=0.0, abs_tol=1e-12
    ):
        raise IdentityRefused("runtime window duration differs from calibration")
    path = Path(str(candidate.get("wav") or ""))
    if not path.is_file() or candidate.get("audio_sha256") != SMR.sha256_file(path):
        raise IdentityRefused("candidate staged WAV is missing or hash-invalid")
    samples, rate = _load_audio(path)
    bounds = candidate_window_bounds(samples.size, rate, window_sec=window_sec)
    if len(bounds) < MIN_CANDIDATE_WINDOWS:
        raise IdentityRefused("candidate has fewer than three full speech windows")
    vectors = []
    for index, (start, end) in enumerate(bounds):
        key = SMR.sha256_text(
            str(candidate["audio_sha256"])
            + f"|{backend.name}|candidate-window-v1|{window_sec}|{index}|{start}|{end}"
        )
        target = cache_root / backend.name / (key + ".npy")
        if target.exists():
            vector = np.load(target, allow_pickle=False)
        else:
            vector = np.asarray(backend.embed(samples[start:end], rate), dtype=np.float32).reshape(-1)
            if vector.size == 0 or not np.all(np.isfinite(vector)):
                raise IdentityRefused("backend emitted an invalid candidate vector")
            _save_npy_atomic(target, vector)
        vectors.append(np.asarray(vector, dtype=np.float32).reshape(-1))
    if len({vector.shape for vector in vectors}) != 1:
        raise IdentityRefused("candidate embedding dimensions are inconsistent")
    return np.stack(vectors), bounds, rate


def _save_comparisons(
    path: Path, scores: np.ndarray, anchor_ids: np.ndarray,
    anchor_members: np.ndarray, anchor_event_ids: np.ndarray,
    bounds: Sequence[tuple[int, int]], sample_rate: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp = Path(raw)
    try:
        with temp.open("wb") as handle:
            np.savez_compressed(
                handle,
                scores=np.asarray(scores, dtype=np.float32),
                anchor_ids=np.asarray(anchor_ids),
                anchor_members=np.asarray(anchor_members),
                anchor_event_ids=np.asarray(anchor_event_ids),
                window_start_sec=np.asarray([start / sample_rate for start, _ in bounds]),
                window_end_sec=np.asarray([end / sample_rate for _, end in bounds]),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _validate_transcript_binding(candidate: dict[str, Any]) -> dict[str, Any]:
    receipt_path = Path(str(candidate.get("asr_transcript_receipt") or ""))
    words_path = Path(str(candidate.get("asr_transcript") or ""))
    if not receipt_path.is_file() or not words_path.is_file():
        raise IdentityRefused("hash-bound Whisper transcript receipt is required")
    receipt = _load_receipt(receipt_path, SMR.ASR_RECEIPT_SCHEMA)
    if (
        receipt.get("status") != "PASS"
        or candidate.get("asr_transcript_receipt_sha256") != SMR.sha256_file(receipt_path)
        or receipt.get("transcript_sha256") != SMR.sha256_file(words_path)
        or candidate.get("asr_transcript_sha256") != receipt.get("transcript_sha256")
        or candidate.get("source_audio_sha256") != receipt.get("source_audio_sha256")
        or not str(receipt.get("reference_url") or candidate.get("reference_url") or "")
    ):
        raise IdentityRefused("Whisper transcript/source/reference binding is stale or incomplete")
    return receipt


def _validate_candidate_cut_binding(
    candidate: dict[str, Any], *, minimum_ratio: float
) -> dict[str, Any]:
    """Prove the identity WAV is the exact secured source PCM frame range."""
    if (
        candidate.get("schema") != SMR.CLIP_SCHEMA
        or candidate.get("status") != "CANDIDATE_AUDIT_REQUIRED"
    ):
        raise IdentityRefused("legacy or non-auditable candidate schema/status")
    try:
        ratio = float(candidate.get("alignment_ratio"))
    except (TypeError, ValueError) as exc:
        raise IdentityRefused("candidate lexical alignment ratio is missing") from exc
    minimum_ratio = validate_locked_alignment_threshold(minimum_ratio)
    if not math.isfinite(ratio) or ratio < minimum_ratio or ratio > 1.0:
        raise IdentityRefused("candidate lexical alignment is below the locked threshold")
    if candidate.get("cut_binding_sha256") != SMR.candidate_cut_binding(candidate):
        raise IdentityRefused("candidate exact-frame cut binding hash mismatch")

    source = Path(str(candidate.get("source_wav") or ""))
    staged = Path(str(candidate.get("wav") or ""))
    source_receipt_path = Path(str(candidate.get("source_receipt") or ""))
    if not source.is_file() or SMR.sha256_file(source) != candidate.get("source_audio_sha256"):
        raise IdentityRefused("secured source WAV is missing or hash-invalid")
    if not staged.is_file() or SMR.sha256_file(staged) != candidate.get("audio_sha256"):
        raise IdentityRefused("candidate staged WAV is missing or hash-invalid")
    if (
        not source_receipt_path.is_file()
        or SMR.sha256_file(source_receipt_path) != candidate.get("source_receipt_sha256")
    ):
        raise IdentityRefused("source acquisition receipt is missing or hash-invalid")
    receipt = _load_receipt(source_receipt_path, "speech-source-media-acquisition-v1")
    if (
        receipt.get("wav_sha256") != candidate.get("source_audio_sha256")
        or Path(str(receipt.get("wav") or "")).resolve() != source.resolve()
        or not str(candidate.get("provider") or "")
        or not str(candidate.get("media_id") or "")
        or str(receipt.get("provider") or "") != str(candidate.get("provider") or "")
        or str(receipt.get("media_id") or "") != str(candidate.get("media_id") or "")
        or not str(receipt.get("reference_url") or candidate.get("reference_url") or "")
    ):
        raise IdentityRefused("source acquisition receipt does not bind the candidate source")
    try:
        rate = int(candidate.get("source_sample_rate"))
        start = int(candidate.get("source_start_frame"))
        frames = int(candidate.get("source_frame_count"))
    except (TypeError, ValueError) as exc:
        raise IdentityRefused("candidate exact frame contract is missing") from exc
    if rate != int(candidate.get("sample_rate") or 0) or rate != 16000:
        raise IdentityRefused("candidate sample-rate contract is invalid")
    _, staged_frames = wav_contract(staged)
    if staged_frames != frames:
        raise IdentityRefused("candidate staged WAV has trailing or missing frames")
    source_pcm = SMR.wav_pcm_slice_sha256(source, start, frames, sample_rate=rate)
    staged_pcm = SMR.wav_pcm_slice_sha256(staged, 0, frames, sample_rate=rate)
    if (
        source_pcm != candidate.get("source_slice_pcm_sha256")
        or staged_pcm != candidate.get("staged_pcm_sha256")
        or source_pcm != staged_pcm
    ):
        raise IdentityRefused("candidate staged PCM is not the exact secured source slice")
    return {
        "source_start_frame": start,
        "source_frame_count": frames,
        "source_sample_rate": rate,
        "source_slice_pcm_sha256": source_pcm,
        "candidate_audio_sha256": str(candidate["audio_sha256"]),
        "candidate_cut_binding_sha256": str(candidate["cut_binding_sha256"]),
    }


def validate_candidate_provenance_binding(
    candidate: dict[str, Any], inventory_by_package: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Bind a candidate to the current crawler inventory and media discovery.

    Program, title, date, and airtime are descriptive metadata only.  They do
    not participate in speaker acceptance.  The acceptance evidence is the
    secured media/transcript chain, lexical localization, and exhaustive
    multi-anchor voice consensus.
    """
    package = str(candidate.get("package") or "")
    record = inventory_by_package.get(package)
    if not record or record.get("package_id") != SMR.package_id(package):
        raise IdentityRefused("candidate is absent from the bound crawler inventory")
    try:
        provenance = SMR.bound_candidate_provenance(record, candidate)
    except SMR.RecoveryError as exc:
        raise IdentityRefused(str(exc)) from exc
    return {
        "inventory_package_id": record["package_id"],
        "inventory_row_sha256": SMR.sha256_json(record),
        "provenance_kind": provenance["kind"],
        "provenance_evidence_sha256": provenance["binding_sha256"],
        "candidate_provenance_pass": True,
        "program_airtime_used_for_identity": False,
    }


def run_identify(args: argparse.Namespace) -> int:
    minimum_ratio = validate_locked_alignment_threshold(args.minimum_ratio)
    anchor_receipt = _load_receipt(Path(args.anchor_receipt), ANCHOR_RECEIPT_SCHEMA)
    if anchor_receipt.get("status") != "PASS":
        raise IdentityRefused("complete anchor embeddings are required for identification")
    vault_path = Path(str(anchor_receipt.get("combined_vectors") or ""))
    if anchor_receipt.get("combined_vectors_sha256") != SMR.sha256_file(vault_path):
        raise IdentityRefused("anchor vectors changed after calibration")
    model_manifest = Path(str(anchor_receipt.get("model_manifest") or ""))
    calibration_window_manifest = Path(
        str(anchor_receipt.get("calibration_window_manifest") or "")
    )
    if (
        not model_manifest.is_file()
        or anchor_receipt.get("model_manifest_sha256") != SMR.sha256_file(model_manifest)
        or not calibration_window_manifest.is_file()
        or anchor_receipt.get("calibration_window_manifest_sha256")
        != SMR.sha256_file(calibration_window_manifest)
        or not math.isclose(float(args.window_sec), CALIBRATION_WINDOW_SEC, abs_tol=1e-12)
    ):
        raise IdentityRefused("identity runtime does not match the calibrated model/window contract")
    calibration = _load_receipt(Path(args.calibration), CALIBRATION_SCHEMA)
    if (
        calibration.get("status") != "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
        or calibration.get("per_claim_thresholds_status")
        != "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
        or int(calibration.get("per_claim_false_accepts", -1)) != 0
        or calibration.get("anchor_vectors_sha256") != anchor_receipt.get("combined_vectors_sha256")
        or calibration.get("calibration_window_manifest_sha256")
        != anchor_receipt.get("calibration_window_manifest_sha256")
        or calibration.get("model_manifest_sha256")
        != anchor_receipt.get("model_manifest_sha256")
        or calibration.get("scoring_contract_sha256")
        != identity_scoring_contract_sha256()
    ):
        raise IdentityRefused("zero-false-accept calibration is absent, failed, or stale")
    vault = load_vault(vault_path)
    thread_state = threading.local()

    def thread_backend() -> Any:
        backend = getattr(thread_state, "backend", None)
        if backend is None:
            # Separate hash-pinned model instances let each CPU worker embed
            # independently.  Thresholds, vectors, and decisions are unchanged;
            # extra RAM buys throughput rather than relaxed evidence.
            backend = _production_backend(args)
            thread_state.backend = backend
        return backend
    inventory_rows = SMR.read_jsonl(Path(args.inventory), required=True)
    inventory_by_package = {
        str(row.get("package") or ""): row for row in inventory_rows
    }
    candidate_manifest_path = Path(args.candidate_manifest)
    candidate_receipt_path = Path(args.candidate_receipt)
    candidates = SMR.read_jsonl(candidate_manifest_path, required=True)
    candidate_receipt = _load_receipt(
        candidate_receipt_path, SMR.CANDIDATE_RECEIPT_SCHEMA
    )
    if (
        candidate_receipt.get("status") != "AUDIT_REQUIRED"
        or candidate_receipt.get("manifest_sha256") != SMR.sha256_file(candidate_manifest_path)
        or int(candidate_receipt.get("candidate_clips") or -1) != len(candidates)
        or candidate_receipt.get("inventory_sha256") != SMR.sha256_file(Path(args.inventory))
        or candidate_receipt.get("exact_frame_cut_binding_required") is not True
    ):
        raise IdentityRefused("candidate manifest receipt is missing, stale, or lacks exact-frame binding")
    selected = {value.strip() for value in args.source.split(",") if value.strip()}
    if not selected and not args.include_remaining:
        selected = set(SOURCE_PRIORITY)
    candidates = [
        row for row in candidates
        if (not selected or str(row.get("source_family") or "") in selected)
    ]
    candidates.sort(key=lambda row: (
        source_priority(str(row.get("source_family") or "")),
        str(row.get("date") or ""), str(row.get("clip_id") or ""),
    ))
    decisions = {
        str(row.get("clip_id") or ""): row
        for row in SMR.read_jsonl(Path(args.decision_manifest))
    }
    eligible_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        clip_id = str(candidate.get("clip_id") or "")
        prior = decisions.get(clip_id)
        if prior and not args.refresh:
            prior_comparison = Path(str(prior.get("comparison_matrix") or ""))
            try:
                current_provenance_binding = validate_candidate_provenance_binding(
                    candidate, inventory_by_package
                )
                _validate_transcript_binding(candidate)
                _validate_candidate_cut_binding(
                    candidate, minimum_ratio=minimum_ratio
                )
                current_candidate_valid = True
            except (IdentityRefused, OSError, ValueError):
                current_provenance_binding = {}
                current_candidate_valid = False
            reusable = (
                current_candidate_valid
                and candidate.get("schema") == SMR.CLIP_SCHEMA
                and candidate.get("status") == "CANDIDATE_AUDIT_REQUIRED"
                and candidate.get("clip_sha256") == SMR._candidate_hash(candidate)
                and prior.get("schema") == IDENTITY_SCHEMA
                and prior.get("identity_row_sha256") == _row_hash(prior, "identity_row_sha256")
                and prior.get("candidate_clip_sha256") == candidate.get("clip_sha256")
                and prior.get("anchor_vectors_sha256") == anchor_receipt.get("combined_vectors_sha256")
                and prior.get("calibration_sha256") == SMR.sha256_file(Path(args.calibration))
                and prior.get("scoring_contract_sha256")
                == identity_scoring_contract_sha256()
                and prior.get("candidate_cut_binding_sha256") == candidate.get("cut_binding_sha256")
                and prior_comparison.is_file()
                and SMR.sha256_file(prior_comparison) == prior.get("comparison_matrix_sha256")
                and prior.get("inventory_row_sha256")
                == current_provenance_binding.get("inventory_row_sha256")
                and prior.get("provenance_evidence_sha256")
                == current_provenance_binding.get("provenance_evidence_sha256")
            )
            if reusable:
                continue
        if args.max_candidates and len(eligible_candidates) >= args.max_candidates:
            break
        eligible_candidates.append(candidate)

    def identify_one(candidate: dict[str, Any]) -> dict[str, Any]:
        clip_id = str(candidate.get("clip_id") or "")
        try:
            if candidate.get("clip_sha256") != SMR._candidate_hash(candidate):
                raise IdentityRefused("candidate row hash mismatch")
            provenance_binding = validate_candidate_provenance_binding(
                candidate, inventory_by_package
            )
            transcript_receipt = _validate_transcript_binding(candidate)
            cut_binding = _validate_candidate_cut_binding(
                candidate, minimum_ratio=minimum_ratio
            )
            vectors, bounds, rate = _candidate_vectors(
                candidate, thread_backend(), Path(args.candidate_cache), args.window_sec
            )
            decision = evaluate_identity(
                vectors,
                str(candidate.get("member") or ""),
                vault["vectors"],
                vault["members"].tolist(),
                set(anchor_receipt.get("eligible_targets") or []),
                calibration,
                vault["anchor_ids"].tolist(),
                vault["event_ids"].tolist(),
            )
            coverage = candidate_identity_coverage(
                bounds,
                int(cut_binding["source_frame_count"]),
                rate,
                args.window_sec,
            )
            if (
                decision.get("status") == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
                and not coverage["full_candidate_identity_coverage"]
            ):
                # All scored windows passed, but the candidate also contains
                # an unscored tail.  Preserve only the exact passing run;
                # never promote the untested fragment with the whole turn.
                decision["status"] = (
                    "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
                )
                decision["unanimous_windows"] = False
                decision.setdefault("reasons", []).append(
                    "UNTESTED_TRAILING_FRAGMENT_EXCLUDED"
                )
            decision.update(coverage)
            for run in decision.get("accepted_runs") or []:
                start_window = int(run["start_window"])
                end_window = int(run["end_window_exclusive"])
                relative_start_frame = int(bounds[start_window][0])
                relative_end_frame = int(bounds[end_window - 1][1])
                run.update({
                    "relative_start_frame": relative_start_frame,
                    "relative_frame_count": relative_end_frame - relative_start_frame,
                    "source_start_frame": int(candidate["source_start_frame"])
                    + relative_start_frame,
                    "source_frame_count": relative_end_frame - relative_start_frame,
                    "sample_rate": rate,
                    "parent_candidate_clip_sha256": candidate["clip_sha256"],
                    "scoring_contract_sha256": identity_scoring_contract_sha256(),
                })
                run["identity_run_sha256"] = _row_hash(run, "identity_run_sha256")
            normalized_candidate = vectors / np.maximum(
                np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12
            )
            normalized_anchors = vault["vectors"] / np.maximum(
                np.linalg.norm(vault["vectors"], axis=1, keepdims=True), 1e-12
            )
            scores = normalized_candidate @ normalized_anchors.T
            comparison_path = Path(args.comparison_root) / (
                SMR.sha256_text(clip_id) + ".npz"
            )
            _save_comparisons(
                comparison_path, scores, vault["anchor_ids"], vault["members"],
                vault["event_ids"], bounds, rate
            )
            decision.update({
                "clip_id": clip_id,
                "candidate_clip_sha256": candidate["clip_sha256"],
                "claimed_member_source": str(
                    candidate.get("identity_claim_source")
                    or "crawler-transcript-claim-only"
                ),
                "lexical_alignment_ratio": candidate.get("alignment_ratio"),
                "transcript_receipt": candidate["asr_transcript_receipt"],
                "transcript_receipt_sha256": candidate["asr_transcript_receipt_sha256"],
                "transcript_sha256": transcript_receipt["transcript_sha256"],
                "anchor_manifest_sha256": anchor_receipt["manifest_sha256"],
                "anchor_vectors_sha256": anchor_receipt["combined_vectors_sha256"],
                "calibration_sha256": SMR.sha256_file(Path(args.calibration)),
                "calibration_window_manifest_sha256": anchor_receipt[
                    "calibration_window_manifest_sha256"
                ],
                "model_manifest_sha256": anchor_receipt["model_manifest_sha256"],
                "scoring_contract_sha256": identity_scoring_contract_sha256(),
                "comparison_matrix": str(comparison_path.resolve()),
                "comparison_matrix_sha256": SMR.sha256_file(comparison_path),
                "comparisons": int(scores.size),
                "candidate_windows": len(bounds),
                "reference_url": str(candidate.get("reference_url") or transcript_receipt["reference_url"]),
                **cut_binding,
                **provenance_binding,
                "identified_at": SMR.utc_now(),
            })
        except (IdentityRefused, OSError, ValueError, BackendRefused) as exc:
            decision = {
                "schema": IDENTITY_SCHEMA,
                "clip_id": clip_id,
                "claimed_member": str(candidate.get("member") or ""),
                "status": "UNRESOLVED",
                "reasons": [str(exc)],
                "operational_confidence_label": "100_PERCENT_STRUCTURAL_CONSENSUS",
                "probability_claim": False,
                "scoring_contract_sha256": identity_scoring_contract_sha256(),
                "identified_at": SMR.utc_now(),
            }
        decision["identity_row_sha256"] = _row_hash(decision, "identity_row_sha256")
        return decision

    workers = max(1, int(args.workers))
    executor = (
        concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        if workers > 1 else None
    )

    def completed_decisions() -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
        if executor is None:
            for candidate in eligible_candidates:
                yield candidate, identify_one(candidate)
            return
        future_candidates = {
            executor.submit(identify_one, candidate): candidate
            for candidate in eligible_candidates
        }
        for future in concurrent.futures.as_completed(future_candidates):
            yield future_candidates[future], future.result()

    processed = 0
    try:
        for candidate, decision in completed_decisions():
            clip_id = str(candidate.get("clip_id") or "")
            decisions[clip_id] = decision
            processed += 1
            if (
                processed % max(1, int(args.checkpoint_every)) == 0
                or processed == len(eligible_candidates)
            ):
                SMR.write_jsonl_atomic(
                    Path(args.decision_manifest),
                    sorted(decisions.values(), key=lambda row: str(row.get("clip_id") or "")),
                )
            print(f"IDENTIFY {clip_id} status={decision['status']}", flush=True)
    finally:
        if executor is not None:
            executor.shutdown(wait=True)
    if not Path(args.decision_manifest).exists():
        SMR.write_jsonl_atomic(Path(args.decision_manifest), [])
    values = list(decisions.values())
    receipt = {
        "schema": IDENTITY_RECEIPT_SCHEMA,
        "status": "PASS",
        "generated_at": SMR.utc_now(),
        "manifest": str(Path(args.decision_manifest).resolve()),
        "manifest_sha256": SMR.sha256_file(Path(args.decision_manifest)),
        "decisions": len(values),
        "accepted": sum(row.get("status") == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS" for row in values),
        "partially_accepted": sum(
            row.get("status") == "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
            for row in values
        ),
        "accepted_runs": sum(int(row.get("accepted_run_count") or 0) for row in values),
        "unresolved": sum(
            row.get("status") not in {
                "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
                "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
            }
            for row in values
        ),
        "source_order": list(SOURCE_PRIORITY),
        "all_anchor_comparisons_persisted": True,
        "anchor_vectors_sha256": anchor_receipt["combined_vectors_sha256"],
        "calibration_sha256": SMR.sha256_file(Path(args.calibration)),
        "calibration": str(Path(args.calibration).resolve()),
        "calibration_window_manifest_sha256": anchor_receipt[
            "calibration_window_manifest_sha256"
        ],
        "model_manifest_sha256": anchor_receipt["model_manifest_sha256"],
        "scoring_contract_sha256": identity_scoring_contract_sha256(),
        "identity_schema": IDENTITY_SCHEMA,
        "inventory_sha256": SMR.sha256_file(Path(args.inventory)),
        "inventory": str(Path(args.inventory).resolve()),
        "candidate_manifest": str(candidate_manifest_path.resolve()),
        "candidate_manifest_sha256": SMR.sha256_file(candidate_manifest_path),
        "candidate_receipt": str(candidate_receipt_path.resolve()),
        "candidate_receipt_sha256": SMR.sha256_file(candidate_receipt_path),
        "minimum_alignment_ratio": minimum_ratio,
        "exact_frame_cut_binding_required": True,
    }
    SMR.write_json_atomic(Path(args.decision_receipt), receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


def validated_merge_source_overlap(
    chain_sources: set[str], sources_seen: set[str], *, allow_overlap: bool
) -> set[str]:
    """Return explicit overlap while preserving the default source-partition gate."""
    overlap = chain_sources & sources_seen
    if not chain_sources or (overlap and not allow_overlap):
        raise IdentityRefused("merge input source-family partitions overlap")
    return overlap


def source_frame_intervals_overlap(
    left_start: int, left_count: int, right_start: int, right_count: int
) -> bool:
    """Return whether two positive half-open source-frame intervals intersect."""
    if min(left_start, right_start) < 0 or min(left_count, right_count) <= 0:
        raise IdentityRefused("candidate source-frame interval is invalid")
    return max(left_start, right_start) < min(
        left_start + left_count, right_start + right_count
    )


def validated_candidate_frame_interval(
    intervals_by_source: dict[str, list[tuple[int, int]]],
    source_audio_sha256: str,
    start_frame: int,
    frame_count: int,
) -> None:
    """Add one interval only when it is disjoint from every prior interval."""
    if not source_audio_sha256:
        raise IdentityRefused("candidate source audio hash is missing")
    prior = intervals_by_source.setdefault(source_audio_sha256, [])
    if any(
        source_frame_intervals_overlap(start_frame, frame_count, old_start, old_count)
        for old_start, old_count in prior
    ):
        raise IdentityRefused("merge candidate source-frame intervals overlap")
    prior.append((start_frame, frame_count))


def run_merge(args: argparse.Namespace) -> int:
    """Merge isolated candidate+identity chains into one exact cumulative chain."""
    identity_receipts = [Path(value) for value in (args.merge_identity_receipt or [])]
    candidate_receipts = [Path(value) for value in (args.merge_candidate_receipt or [])]
    if len(identity_receipts) < 2 or len(identity_receipts) != len(candidate_receipts):
        raise IdentityRefused(
            "merge requires matching repeated candidate and identity receipt arguments"
        )
    minimum_ratio = validate_locked_alignment_threshold(args.minimum_ratio)
    calibration_path = Path(args.calibration)
    inventory_path = Path(args.inventory)
    calibration_sha = SMR.sha256_file(calibration_path)
    inventory_sha = SMR.sha256_file(inventory_path)
    inventory_rows = SMR.read_jsonl(inventory_path, required=True)
    inventory_packages = {str(row.get("package") or "") for row in inventory_rows}

    candidate_by_id: dict[str, dict[str, Any]] = {}
    decision_by_id: dict[str, dict[str, Any]] = {}
    candidate_audio: set[str] = set()
    candidate_frames: set[tuple[str, int, int]] = set()
    candidate_intervals: dict[str, list[tuple[int, int]]] = {}
    prior_chain_intervals: dict[str, list[tuple[int, int]]] = {}
    sources_seen: set[str] = set()
    overlapping_sources: set[str] = set()
    excluded_overlap_candidates: list[dict[str, Any]] = []
    chain_ledger: list[dict[str, Any]] = []
    common_identity_fields: dict[str, Any] | None = None
    common_keys = (
        "anchor_vectors_sha256", "calibration_window_manifest_sha256",
        "model_manifest_sha256", "scoring_contract_sha256",
    )
    for candidate_receipt_path, identity_receipt_path in zip(
            candidate_receipts, identity_receipts):
        candidate_receipt = _load_receipt(
            candidate_receipt_path, SMR.CANDIDATE_RECEIPT_SCHEMA
        )
        identity_receipt = _load_receipt(identity_receipt_path, IDENTITY_RECEIPT_SCHEMA)
        candidate_manifest = Path(str(candidate_receipt.get("manifest") or ""))
        identity_manifest = Path(str(identity_receipt.get("manifest") or ""))
        input_inventory = Path(str(candidate_receipt.get("inventory") or ""))
        if not all(path.is_absolute() and path.is_file() for path in (
                candidate_manifest, identity_manifest, input_inventory)):
            raise IdentityRefused("merge input receipt points to a missing/nonabsolute artifact")
        candidate_rows = SMR.read_jsonl(candidate_manifest, required=True)
        decision_rows = SMR.read_jsonl(identity_manifest, required=True)
        candidate_manifest_sha = SMR.sha256_file(candidate_manifest)
        candidate_receipt_sha = SMR.sha256_file(candidate_receipt_path)
        identity_manifest_sha = SMR.sha256_file(identity_manifest)
        identity_receipt_sha = SMR.sha256_file(identity_receipt_path)
        input_inventory_sha = SMR.sha256_file(input_inventory)
        if (
            candidate_receipt.get("status") != "AUDIT_REQUIRED"
            or candidate_receipt.get("manifest_sha256") != candidate_manifest_sha
            or int(candidate_receipt.get("candidate_clips", -1)) != len(candidate_rows)
            or candidate_receipt.get("inventory_sha256") != input_inventory_sha
            or candidate_receipt.get("exact_frame_cut_binding_required") is not True
            or identity_receipt.get("status") != "PASS"
            or identity_receipt.get("identity_schema") != IDENTITY_SCHEMA
            or identity_receipt.get("manifest_sha256") != identity_manifest_sha
            or int(identity_receipt.get("decisions", -1)) != len(decision_rows)
            or identity_receipt.get("candidate_manifest_sha256") != candidate_manifest_sha
            or identity_receipt.get("candidate_receipt_sha256") != candidate_receipt_sha
            or identity_receipt.get("inventory_sha256") != input_inventory_sha
            or identity_receipt.get("calibration_sha256") != calibration_sha
            or identity_receipt.get("scoring_contract_sha256")
            != identity_scoring_contract_sha256()
            or identity_receipt.get("exact_frame_cut_binding_required") is not True
            or identity_receipt.get("all_anchor_comparisons_persisted") is not True
            or validate_locked_alignment_threshold(
                identity_receipt.get("minimum_alignment_ratio")) != minimum_ratio
        ):
            raise IdentityRefused("merge input candidate/identity receipt chain is stale")
        identity_fields = {key: identity_receipt.get(key) for key in common_keys}
        if common_identity_fields is None:
            common_identity_fields = identity_fields
        elif identity_fields != common_identity_fields:
            raise IdentityRefused("merge input identity chains use different anchor/model contracts")

        chain_candidate_ids = set()
        chain_excluded_ids = set()
        chain_sources = set()
        chain_intervals: list[tuple[str, int, int]] = []
        prior_chain_candidate_ids = set(candidate_by_id)
        for candidate in candidate_rows:
            clip_id = str(candidate.get("clip_id") or "")
            source = str(candidate.get("source_family") or "")
            audio = str(candidate.get("audio_sha256") or "")
            frame_identity = (
                str(candidate.get("source_audio_sha256") or ""),
                int(candidate.get("source_start_frame", -1)),
                int(candidate.get("source_frame_count", -1)),
            )
            try:
                alignment = float(candidate.get("alignment_ratio"))
            except (TypeError, ValueError) as exc:
                raise IdentityRefused("merge candidate alignment is missing") from exc
            if (
                candidate.get("schema") != SMR.CLIP_SCHEMA
                or candidate.get("status") != "CANDIDATE_AUDIT_REQUIRED"
                or candidate.get("clip_sha256") != SMR._candidate_hash(candidate)
                or candidate.get("cut_binding_sha256") != SMR.candidate_cut_binding(candidate)
                or not clip_id or not source or not audio
                or not math.isfinite(alignment) or alignment < minimum_ratio or alignment > 1.0
                or str(candidate.get("package") or "") not in inventory_packages
                or clip_id in candidate_by_id or audio in candidate_audio
                or frame_identity in candidate_frames
            ):
                raise IdentityRefused(
                    "merge candidates are stale, duplicate, or absent from the cumulative inventory"
                )
            overlap_ids = [
                old_id
                for old_id, old_candidate in candidate_by_id.items()
                if old_id in prior_chain_candidate_ids
                and str(old_candidate.get("source_audio_sha256") or "") == frame_identity[0]
                and source_frame_intervals_overlap(
                    frame_identity[1], frame_identity[2],
                    int(old_candidate.get("source_start_frame", -1)),
                    int(old_candidate.get("source_frame_count", -1)),
                )
            ]
            if overlap_ids:
                same_member = all(
                    str(candidate_by_id[old_id].get("member") or "")
                    == str(candidate.get("member") or "")
                    for old_id in overlap_ids
                )
                if (
                    bool(getattr(args, "exclude_same_member_overlap", False))
                    and bool(getattr(args, "allow_source_overlap", False))
                    and same_member
                ):
                    chain_excluded_ids.add(clip_id)
                    excluded_overlap_candidates.append({
                        "clip_id": clip_id,
                        "member": str(candidate.get("member") or ""),
                        "source_audio_sha256": frame_identity[0],
                        "source_start_frame": frame_identity[1],
                        "source_frame_count": frame_identity[2],
                        "overlapping_prior_clip_ids": sorted(overlap_ids),
                        "reason": "SAME_MEMBER_SOURCE_FRAME_OVERLAP_ALREADY_SECURED",
                    })
                    continue
                raise IdentityRefused("merge candidate source-frame intervals overlap across input chains")
            candidate_intervals.setdefault(frame_identity[0], []).append(
                (frame_identity[1], frame_identity[2])
            )
            chain_intervals.append(frame_identity)
            candidate_by_id[clip_id] = candidate
            candidate_audio.add(audio)
            candidate_frames.add(frame_identity)
            chain_candidate_ids.add(clip_id)
            chain_sources.add(source)
        for source_audio_sha256, start_frame, frame_count in chain_intervals:
            prior_chain_intervals.setdefault(source_audio_sha256, []).append(
                (start_frame, frame_count)
            )
        overlap = validated_merge_source_overlap(
            chain_sources, sources_seen,
            allow_overlap=bool(getattr(args, "allow_source_overlap", False)),
        )
        overlapping_sources.update(overlap)
        sources_seen.update(chain_sources)
        for decision in decision_rows:
            clip_id = str(decision.get("clip_id") or "")
            if clip_id in chain_excluded_ids:
                continue
            candidate = candidate_by_id.get(clip_id)
            if clip_id not in chain_candidate_ids or clip_id in decision_by_id or candidate is None:
                raise IdentityRefused("merge identity decision binding is stale or duplicated")
            validate_merge_identity_decision(decision, candidate, calibration_sha)
            decision_by_id[clip_id] = decision
        chain_ledger.append({
            "source_families": sorted(chain_sources),
            "candidate_manifest": str(candidate_manifest.resolve()),
            "candidate_manifest_sha256": candidate_manifest_sha,
            "candidate_receipt": str(candidate_receipt_path.resolve()),
            "candidate_receipt_sha256": candidate_receipt_sha,
            "candidate_clips": len(candidate_rows),
            "included_candidate_clips": len(chain_candidate_ids),
            "excluded_same_member_overlap_clips": len(chain_excluded_ids),
            "identity_manifest": str(identity_manifest.resolve()),
            "identity_manifest_sha256": identity_manifest_sha,
            "identity_receipt": str(identity_receipt_path.resolve()),
            "identity_receipt_sha256": identity_receipt_sha,
            "identity_decisions": len(decision_rows),
            "input_inventory": str(input_inventory.resolve()),
            "input_inventory_sha256": input_inventory_sha,
        })

    chain_ledger.sort(key=lambda row: row["source_families"])
    excluded_overlap_candidates.sort(key=lambda row: row["clip_id"])
    candidates = sorted(candidate_by_id.values(), key=lambda row: str(row["clip_id"]))
    decisions = sorted(decision_by_id.values(), key=lambda row: str(row["clip_id"]))
    candidate_manifest_out = Path(args.candidate_manifest)
    candidate_receipt_out = Path(args.candidate_receipt)
    identity_manifest_out = Path(args.decision_manifest)
    identity_receipt_out = Path(args.decision_receipt)
    SMR.write_jsonl_atomic(candidate_manifest_out, candidates)
    candidate_receipt = {
        "schema": SMR.CANDIDATE_RECEIPT_SCHEMA,
        "status": "AUDIT_REQUIRED",
        "generated_at": SMR.utc_now(),
        "manifest": str(candidate_manifest_out.resolve()),
        "manifest_sha256": SMR.sha256_file(candidate_manifest_out),
        "candidate_clips": len(candidates),
        "clip_schema": SMR.CLIP_SCHEMA,
        "clip_schema_counts": {SMR.CLIP_SCHEMA: len(candidates)},
        "exact_frame_candidates": len(candidates),
        "inventory": str(inventory_path.resolve()),
        "inventory_sha256": inventory_sha,
        "exact_frame_cut_binding_required": True,
        "accepted_manifest_modified": False,
        "cumulative_chain_merge": True,
        "source_partitions_disjoint": not overlapping_sources,
        "input_candidate_audio_frame_sets_disjoint": all(
            not source_frame_intervals_overlap(a_start, a_count, b_start, b_count)
            for intervals in candidate_intervals.values()
            for index, (a_start, a_count) in enumerate(intervals)
            for b_start, b_count in intervals[index + 1:]
        ),
        "overlapping_source_families": sorted(overlapping_sources),
        "all_input_candidate_identity_receipts_verified": True,
        "excluded_same_member_overlap_candidates": excluded_overlap_candidates,
        "excluded_same_member_overlap_candidates_sha256": SMR.sha256_json(
            excluded_overlap_candidates
        ),
        "input_chains": chain_ledger,
        "input_chains_sha256": SMR.sha256_json(chain_ledger),
    }
    SMR.write_json_atomic(candidate_receipt_out, candidate_receipt)
    SMR.write_jsonl_atomic(identity_manifest_out, decisions)
    common_identity_fields = common_identity_fields or {}
    identity_receipt = {
        "schema": IDENTITY_RECEIPT_SCHEMA,
        "status": "PASS",
        "generated_at": SMR.utc_now(),
        "manifest": str(identity_manifest_out.resolve()),
        "manifest_sha256": SMR.sha256_file(identity_manifest_out),
        "decisions": len(decisions),
        "accepted": sum(row.get("status") == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
                        for row in decisions),
        "partially_accepted": sum(
            row.get("status") == "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
            for row in decisions),
        "accepted_runs": sum(int(row.get("accepted_run_count") or 0) for row in decisions),
        "unresolved": sum(row.get("status") not in {
            "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
            "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
        } for row in decisions),
        "source_order": list(SOURCE_PRIORITY),
        "all_anchor_comparisons_persisted": True,
        **common_identity_fields,
        "calibration_sha256": calibration_sha,
        "scoring_contract_sha256": identity_scoring_contract_sha256(),
        "identity_schema": IDENTITY_SCHEMA,
        "inventory_sha256": inventory_sha,
        "candidate_manifest": str(candidate_manifest_out.resolve()),
        "candidate_manifest_sha256": SMR.sha256_file(candidate_manifest_out),
        "candidate_receipt": str(candidate_receipt_out.resolve()),
        "candidate_receipt_sha256": SMR.sha256_file(candidate_receipt_out),
        "minimum_alignment_ratio": minimum_ratio,
        "exact_frame_cut_binding_required": True,
        "cumulative_chain_merge": True,
        "source_partitions_disjoint": not overlapping_sources,
        "input_candidate_audio_frame_sets_disjoint": True,
        "overlapping_source_families": sorted(overlapping_sources),
        "all_input_candidate_identity_receipts_verified": True,
        "excluded_same_member_overlap_candidates": excluded_overlap_candidates,
        "excluded_same_member_overlap_candidates_sha256": SMR.sha256_json(
            excluded_overlap_candidates
        ),
        "input_chains": chain_ledger,
        "input_chains_sha256": SMR.sha256_json(chain_ledger),
    }
    SMR.write_json_atomic(identity_receipt_out, identity_receipt)
    print(json.dumps({
        "status": "PASS", "candidate_clips": len(candidates),
        "identity_decisions": len(decisions), "source_families": sorted(sources_seen),
        "overlapping_source_families": sorted(overlapping_sources),
        "candidate_manifest_sha256": candidate_receipt["manifest_sha256"],
        "identity_manifest_sha256": identity_receipt["manifest_sha256"],
        "input_chains_sha256": identity_receipt["input_chains_sha256"],
    }, sort_keys=True))
    return 0


def _write_final_cut(
    candidate: dict[str, Any], target: Path, *, minimum_ratio: float
) -> tuple[str, int, float]:
    _validate_candidate_cut_binding(candidate, minimum_ratio=minimum_ratio)
    staged = Path(str(candidate.get("wav") or ""))
    expected = str(candidate.get("audio_sha256") or "")
    frames = int(candidate.get("source_frame_count") or 0)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or SMR.sha256_file(target) != expected:
        fd, raw = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        os.close(fd)
        temp = Path(raw)
        try:
            shutil.copyfile(staged, temp)
            if SMR.sha256_file(temp) != expected:
                raise IdentityRefused("copied final WAV differs from the identity-tested candidate")
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()
    duration, written_frames = wav_contract(target)
    if written_frames != frames:
        raise IdentityRefused("final cut frame count mismatch")
    digest = SMR.sha256_file(target)
    if digest != expected:
        raise IdentityRefused("final WAV hash differs from the identity-tested candidate")
    return digest, written_frames, duration


def validate_identity_run_geometry(
    candidate: dict[str, Any], run: dict[str, Any]
) -> tuple[int, int]:
    relative_start = int(run.get("relative_start_frame") or 0)
    frame_count = int(run.get("relative_frame_count") or 0)
    window_count = int(run.get("window_count") or 0)
    start_window = int(run.get("start_window") or 0)
    end_window = int(run.get("end_window_exclusive") or 0)
    run_source_start = run.get("source_start_frame")
    exact_width = int(round(CALIBRATION_WINDOW_SEC * 16000))
    if (
        relative_start < 0
        or frame_count <= 0
        or window_count < MIN_CANDIDATE_WINDOWS
        or end_window - start_window != window_count
        or relative_start != start_window * exact_width
        or frame_count != window_count * exact_width
        or int(run.get("sample_rate") or 0) != 16000
        or run_source_start is None
        or int(run_source_start)
        != int(candidate.get("source_start_frame") or 0) + relative_start
        or int(run.get("source_frame_count") or -1) != frame_count
        or relative_start + frame_count > int(candidate.get("source_frame_count") or 0)
    ):
        raise IdentityRefused(
            "identity-passing run is not an exact sequence of calibrated windows"
        )
    return relative_start, frame_count


def _write_final_window(
    candidate: dict[str, Any],
    decision: dict[str, Any],
    run: dict[str, Any],
    window_index: int,
    target: Path,
    *,
    minimum_ratio: float,
) -> tuple[str, int, float, str]:
    """Write one independently passing exact window from the candidate PCM."""
    _validate_candidate_cut_binding(candidate, minimum_ratio=minimum_ratio)
    lineage = identity_window_lineage(
        candidate, decision, run, int(window_index)
    )
    relative_start = int(lineage["relative_start_frame"])
    frame_count = int(lineage["source_frame_count"])
    staged = Path(str(candidate.get("wav") or ""))
    expected_pcm = SMR.wav_pcm_slice_sha256(
        staged, relative_start, frame_count, sample_rate=16000
    )
    source_pcm = SMR.wav_pcm_slice_sha256(
        Path(str(candidate.get("source_wav") or "")),
        int(candidate["source_start_frame"]) + relative_start,
        frame_count,
        sample_rate=16000,
    )
    if expected_pcm != source_pcm:
        raise IdentityRefused("identity-passing window differs from the secured source PCM")
    target.parent.mkdir(parents=True, exist_ok=True)
    valid_existing = False
    if target.is_file():
        try:
            _, existing_frames = wav_contract(target)
            valid_existing = (
                existing_frames == frame_count
                and SMR.wav_pcm_slice_sha256(target, 0, frame_count, sample_rate=16000)
                == expected_pcm
            )
        except (IdentityRefused, OSError, ValueError, wave.Error):
            valid_existing = False
    if not valid_existing:
        fd, raw = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        os.close(fd)
        temp = Path(raw)
        try:
            with wave.open(str(staged), "rb") as source_handle:
                source_handle.setpos(relative_start)
                payload = source_handle.readframes(frame_count)
            if len(payload) != frame_count * 2:
                raise IdentityRefused("identity-passing window PCM read is incomplete")
            with wave.open(str(temp), "wb") as target_handle:
                target_handle.setnchannels(1)
                target_handle.setsampwidth(2)
                target_handle.setframerate(16000)
                target_handle.writeframes(payload)
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()
    duration, written_frames = wav_contract(target)
    final_pcm = SMR.wav_pcm_slice_sha256(target, 0, written_frames, sample_rate=16000)
    if written_frames != frame_count or final_pcm != expected_pcm:
        raise IdentityRefused("final identity-passing window is not the exact tested PCM")
    return SMR.sha256_file(target), written_frames, duration, final_pcm


def run_finalize(args: argparse.Namespace) -> int:
    candidate_manifest_path = Path(args.candidate_manifest)
    candidate_receipt_path = Path(args.candidate_receipt)
    candidate_rows = SMR.read_jsonl(candidate_manifest_path, required=True)
    candidate_receipt = _load_receipt(
        candidate_receipt_path, SMR.CANDIDATE_RECEIPT_SCHEMA
    )
    if (
        candidate_receipt.get("status") != "AUDIT_REQUIRED"
        or candidate_receipt.get("manifest_sha256") != SMR.sha256_file(candidate_manifest_path)
        or int(candidate_receipt.get("candidate_clips") or -1) != len(candidate_rows)
        or candidate_receipt.get("inventory_sha256") != SMR.sha256_file(Path(args.inventory))
    ):
        raise IdentityRefused("candidate receipt is stale before finalization")
    candidates = {
        str(row.get("clip_id") or ""): row
        for row in candidate_rows
    }
    if len(candidates) != len(candidate_rows) or "" in candidates:
        raise IdentityRefused("candidate manifest contains duplicate or missing clip IDs")
    decision_manifest_path = Path(args.decision_manifest)
    decision_receipt_path = Path(args.decision_receipt)
    decisions = SMR.read_jsonl(decision_manifest_path, required=True)
    decision_receipt = _load_receipt(decision_receipt_path, IDENTITY_RECEIPT_SCHEMA)
    calibration_path = Path(args.calibration)
    minimum_ratio = validate_locked_alignment_threshold(
        decision_receipt.get("minimum_alignment_ratio")
    )
    if (
        decision_receipt.get("status") != "PASS"
        or decision_receipt.get("manifest_sha256") != SMR.sha256_file(decision_manifest_path)
        or int(decision_receipt.get("decisions") or -1) != len(decisions)
        or decision_receipt.get("candidate_manifest_sha256") != SMR.sha256_file(candidate_manifest_path)
        or decision_receipt.get("candidate_receipt_sha256") != SMR.sha256_file(candidate_receipt_path)
        or decision_receipt.get("inventory_sha256") != SMR.sha256_file(Path(args.inventory))
        or decision_receipt.get("exact_frame_cut_binding_required") is not True
        or decision_receipt.get("identity_schema") != IDENTITY_SCHEMA
        or decision_receipt.get("scoring_contract_sha256")
        != identity_scoring_contract_sha256()
        or not calibration_path.is_file()
        or decision_receipt.get("calibration_sha256") != SMR.sha256_file(calibration_path)
        or not math.isclose(
            float(args.minimum_ratio), minimum_ratio, rel_tol=0.0, abs_tol=1e-12
        )
    ):
        raise IdentityRefused("identity receipt is stale or incomplete before finalization")
    inventory_rows = SMR.read_jsonl(Path(args.inventory), required=True)
    inventory_by_package = {
        str(row.get("package") or ""): row for row in inventory_rows
    }
    selected = {value.strip() for value in args.source.split(",") if value.strip()}
    calibration_sha256 = SMR.sha256_file(calibration_path)
    expected_ids, completeness_errors = expected_secure_final_ids(
        decisions, candidates, calibration_sha256, minimum_ratio
    )
    prior_final_rows = SMR.read_jsonl(Path(args.final_manifest))
    prior_manifest_sha256 = (
        SMR.sha256_file(Path(args.final_manifest)) if Path(args.final_manifest).is_file() else None
    )
    protected_prior: dict[str, dict[str, Any]] = {}
    if selected and prior_final_rows:
        prior_receipt_path = Path(args.final_receipt)
        prior_receipt = _load_receipt(prior_receipt_path, FINAL_RECEIPT_SCHEMA)
        if (
            prior_receipt.get("status") != "PASS"
            or prior_receipt.get("manifest_sha256") != prior_manifest_sha256
            or int(prior_receipt.get("verified_final_clips", -1)) != len(prior_final_rows)
        ):
            raise IdentityRefused(
                "cannot protect cumulative finals without a hash-current prior PASS receipt"
            )
        protected_prior = {
            str(row.get("clip_id") or ""): row
            for row in prior_final_rows
            if str(row.get("source_family") or "") not in selected
        }
        if "" in protected_prior or len(protected_prior) != sum(
            str(row.get("source_family") or "") not in selected for row in prior_final_rows
        ):
            raise IdentityRefused("protected prior finals contain duplicate or missing clip IDs")
    final = {
        str(row.get("clip_id") or ""): row
        for row in prior_final_rows
    }
    final = {
        clip_id: row
        for clip_id, row in final.items()
        if clip_id in expected_ids
        and str(row.get("parent_clip_id") or clip_id) in candidates
        and row.get("candidate_clip_sha256")
        == candidates[str(row.get("parent_clip_id") or clip_id)].get("clip_sha256")
    }
    # A source-scoped finalize may update only the selected source families.
    # Preserve every prior final from all other sources byte-for-byte even if
    # the current candidate/identity inputs are incomplete; the new receipt
    # will fail rather than silently replacing cumulative accepted material.
    final.update(protected_prior)
    processed = 0
    errors: list[dict[str, str]] = list(completeness_errors)
    for decision in decisions:
        if decision.get("status") not in {
            "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
            "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
        }:
            continue
        parent_clip_id = str(decision.get("clip_id") or "")
        candidate = candidates.get(parent_clip_id)
        if selected and (
            not candidate or str(candidate.get("source_family") or "") not in selected
        ):
            continue
        try:
            if (
                decision.get("schema") != IDENTITY_SCHEMA
                or decision.get("scoring_contract_sha256")
                != identity_scoring_contract_sha256()
                or decision.get("calibration_sha256") != SMR.sha256_file(calibration_path)
            ):
                raise IdentityRefused("identity scoring/calibration contract is stale")
            if decision.get("identity_row_sha256") != _row_hash(decision, "identity_row_sha256"):
                raise IdentityRefused("identity decision row hash mismatch")
            if not candidate or candidate.get("clip_sha256") != decision.get("candidate_clip_sha256"):
                raise IdentityRefused("identity decision is not bound to the candidate")
            if (
                decision.get("candidate_audio_sha256") != candidate.get("audio_sha256")
                or decision.get("candidate_cut_binding_sha256") != candidate.get("cut_binding_sha256")
            ):
                raise IdentityRefused("identity decision audio/cut binding is stale")
            comparison = Path(str(decision.get("comparison_matrix") or ""))
            if (
                not comparison.is_file()
                or SMR.sha256_file(comparison) != decision.get("comparison_matrix_sha256")
            ):
                raise IdentityRefused("identity comparison matrix is missing or hash-invalid")
            transcript_receipt = _validate_transcript_binding(candidate)
            cut_binding = _validate_candidate_cut_binding(
                candidate, minimum_ratio=minimum_ratio
            )
            provenance_binding = validate_candidate_provenance_binding(
                candidate, inventory_by_package
            )
            secure_units = accepted_identity_window_units(decision, candidate)
            if (
                not secure_units
                and decision.get("status")
                == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
            ):
                if not whole_candidate_identity_coverage_valid(decision, candidate):
                    raise IdentityRefused(
                        "whole-candidate identity does not cover every PCM frame"
                    )
                secure_units = [{
                    "clip_id": parent_clip_id,
                    "run": None,
                    "window_index": None,
                    "lineage": None,
                }]
            if not secure_units:
                raise IdentityRefused("partial identity decision has no accepted runs")
            member_dir = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(candidate["member"])).strip()
            for unit in secure_units:
                final_clip_id = str(unit["clip_id"])
                run = unit["run"]
                window_index = unit["window_index"]
                lineage = unit["lineage"]
                if args.max_candidates and processed >= args.max_candidates:
                    break
                if final_clip_id in protected_prior:
                    raise IdentityRefused(
                        "selected source final collides with a protected cumulative final ID"
                    )
                target = Path(args.final_root) / str(candidate["source_family"]) / member_dir / (
                    SMR.sha256_text(final_clip_id)[:24] + ".wav"
                )
                if run is None:
                    digest, frames, duration = _write_final_cut(
                        candidate, target, minimum_ratio=minimum_ratio
                    )
                    final_source_start = int(cut_binding["source_start_frame"])
                    final_pcm_sha256 = str(candidate["source_slice_pcm_sha256"])
                    identity_run_sha256 = None
                    features = candidate["features"]
                else:
                    digest, frames, duration, final_pcm_sha256 = _write_final_window(
                        candidate, decision, run, int(window_index), target,
                        minimum_ratio=minimum_ratio,
                    )
                    final_source_start = int(lineage["source_start_frame"])
                    identity_run_sha256 = str(run["identity_run_sha256"])
                    features = None
                source_offset_sec = float(candidate.get("source_offset_sec") or 0.0)
                start_sec = final_source_start / 16000.0 + source_offset_sec
                row = {
                    "schema": FINAL_SCHEMA,
                    "status": "VERIFIED_FINAL",
                    "clip_id": final_clip_id,
                    "parent_clip_id": parent_clip_id,
                    "member": candidate["member"],
                    "source_family": candidate["source_family"],
                    "show": candidate["show"],
                    "date": candidate["date"],
                    "subject": candidate.get("subject"),
                    "setting": candidate.get("setting") or "interview",
                    "package": candidate["package"],
                    "reference_url": str(
                        candidate.get("reference_url") or transcript_receipt["reference_url"]
                    ),
                    "turn_id": candidate["turn_id"],
                    "text_sha256": candidate["text_sha256"],
                    "source_audio_sha256": candidate["source_audio_sha256"],
                    "candidate_clip_sha256": candidate["clip_sha256"],
                    "cut_binding_sha256": candidate["cut_binding_sha256"],
                    "source_start_frame": final_source_start,
                    "source_frame_count": frames,
                    "source_slice_pcm_sha256": final_pcm_sha256,
                    "transcript_sha256": transcript_receipt["transcript_sha256"],
                    "identity_row_sha256": decision["identity_row_sha256"],
                    "identity_run_sha256": identity_run_sha256,
                    "identity_window_index": window_index,
                    "identity_window_sha256": (
                        lineage["identity_window_sha256"] if lineage else None
                    ),
                    "identity_qualifying_run_start_window": (
                        lineage["qualifying_run_start_window"] if lineage else None
                    ),
                    "identity_qualifying_run_end_window_exclusive": (
                        lineage["qualifying_run_end_window_exclusive"] if lineage else None
                    ),
                    "identity_comparison": str(comparison.resolve()),
                    "identity_comparison_sha256": decision["comparison_matrix_sha256"],
                    "identity_contract": "100_PERCENT_STRUCTURAL_CONSENSUS_NOT_PROBABILITY",
                    "calibration_sha256": decision["calibration_sha256"],
                    "scoring_contract_sha256": identity_scoring_contract_sha256(),
                    **provenance_binding,
                    "alignment_ratio": candidate["alignment_ratio"],
                    "start_sec": round(start_sec, 6),
                    "end_sec": round(start_sec + duration, 6),
                    "duration_sec": round(duration, 6),
                    "frames": frames,
                    "sample_rate": 16000,
                    "wav": str(target.resolve()),
                    "audio_sha256": digest,
                    "features": features,
                    "features_status": (
                        "PARENT_EXACT_CUT" if run is None else "RECOMPUTE_FROM_VERIFIED_FINAL"
                    ),
                    "finalized_at": SMR.utc_now(),
                }
                row["final_row_sha256"] = _row_hash(row, "final_row_sha256")
                final[final_clip_id] = row
                processed += 1
                if processed % max(1, int(args.checkpoint_every)) == 0:
                    SMR.write_jsonl_atomic(
                        Path(args.final_manifest),
                        sorted(final.values(), key=lambda value: str(value.get("clip_id") or "")),
                    )
        except (IdentityRefused, OSError, ValueError) as exc:
            errors.append({"clip_id": parent_clip_id, "reason": str(exc)})
    for clip_id, prior in protected_prior.items():
        current = final.get(clip_id)
        if (
            not current
            or current.get("final_row_sha256") != prior.get("final_row_sha256")
            or current != prior
        ):
            errors.append({
                "clip_id": clip_id,
                "reason": "protected cumulative final changed during source-scoped finalization",
            })
    by_source, ledger_errors = cumulative_verified_source_ledger(
        candidate_rows, decisions, list(final.values())
    )
    errors.extend(ledger_errors)
    canonical_audio = {
        str(row.get("sha256") or "")
        for row in SMR.read_jsonl(SMR.CANONICAL_MANIFEST, required=True)
        if row.get("sha256")
    }
    for row in final.values():
        if str(row.get("audio_sha256") or "") in canonical_audio:
            errors.append({
                "clip_id": str(row.get("clip_id") or ""),
                "reason": "verified final audio duplicates the canonical prepared corpus",
            })
    SMR.write_jsonl_atomic(
        Path(args.final_manifest),
        sorted(final.values(), key=lambda row: str(row.get("clip_id") or "")),
    )
    actual_ids = set(final)
    missing_ids = sorted(expected_ids - actual_ids)
    unexpected_ids = sorted(actual_ids - expected_ids)
    errors.extend(
        {"clip_id": clip_id, "reason": "expected secure final is missing"}
        for clip_id in missing_ids
    )
    errors.extend(
        {"clip_id": clip_id, "reason": "final is not implied by current identity decisions"}
        for clip_id in unexpected_ids
    )
    final_id_set_complete = not completeness_errors and actual_ids == expected_ids
    cumulative_source_bindings_complete = (
        not ledger_errors
        and all(value.get("final_id_set_complete") is True for value in by_source.values())
    )
    receipt = {
        "schema": FINAL_RECEIPT_SCHEMA,
        "status": (
            "PASS" if not errors and final_id_set_complete
            and cumulative_source_bindings_complete else "INCOMPLETE"
        ),
        "generated_at": SMR.utc_now(),
        "manifest": str(Path(args.final_manifest).resolve()),
        "manifest_sha256": SMR.sha256_file(Path(args.final_manifest)) if Path(args.final_manifest).exists() else None,
        "verified_final_clips": len(final),
        "expected_final_clips": len(expected_ids),
        "expected_final_ids_sha256": SMR.sha256_json(sorted(expected_ids)),
        "final_id_set_complete": final_id_set_complete,
        "cumulative_source_bindings_complete": cumulative_source_bindings_complete,
        "by_source": by_source,
        "protected_source_families": sorted({
            str(row.get("source_family") or "") for row in protected_prior.values()
        }),
        "protected_prior_manifest_sha256": (
            prior_manifest_sha256 if protected_prior else None
        ),
        "processed": processed,
        "errors": errors,
        "candidate_manifest_sha256": SMR.sha256_file(candidate_manifest_path),
        "candidate_receipt_sha256": SMR.sha256_file(candidate_receipt_path),
        "identity_manifest_sha256": SMR.sha256_file(decision_manifest_path),
        "identity_receipt_sha256": SMR.sha256_file(decision_receipt_path),
        "calibration_sha256": SMR.sha256_file(calibration_path),
        "scoring_contract_sha256": identity_scoring_contract_sha256(),
        "minimum_alignment_ratio": minimum_ratio,
        "identity_schema": IDENTITY_SCHEMA,
        "final_schema": FINAL_SCHEMA,
        "inventory_sha256": SMR.sha256_file(Path(args.inventory)),
        # Final rows are now independent identity-tested windows.  The two
        # legacy parent-audio assertions would be false for those rows and
        # must not survive as misleading receipt claims.
        "whole_final_audio_equals_identity_candidate_required": False,
        "partial_final_audio_equals_identity_tested_run_required": False,
        "final_window_audio_equals_identity_tested_window_required": True,
        "independent_identity_window_finalization": True,
        "final_audio_matches_source_frame_binding_required": True,
        "canonical_source_overlap_requires_record_supersession": True,
        "reference_url_required": True,
        "source_audio_cleanup_authorized": False,
    }
    SMR.write_json_atomic(Path(args.final_receipt), receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if not errors else 5


def run_cleanup(args: argparse.Namespace) -> int:
    candidate_manifest_path = Path(args.candidate_manifest)
    candidate_receipt_path = Path(args.candidate_receipt)
    decision_manifest_path = Path(args.decision_manifest)
    decision_receipt_path = Path(args.decision_receipt)
    final_manifest_path = Path(args.final_manifest)
    final_receipt_path = Path(args.final_receipt)
    calibration_path = Path(args.calibration)
    candidates = SMR.read_jsonl(candidate_manifest_path, required=True)
    decision_rows = SMR.read_jsonl(decision_manifest_path, required=True)
    final_rows = SMR.read_jsonl(final_manifest_path, required=True)
    candidate_by_clip = {
        str(row.get("clip_id") or ""): row for row in candidates
    }
    calibration_sha256 = (
        SMR.sha256_file(calibration_path) if calibration_path.is_file() else ""
    )
    candidate_receipt = _load_receipt(
        candidate_receipt_path, SMR.CANDIDATE_RECEIPT_SCHEMA
    )
    decision_receipt = _load_receipt(decision_receipt_path, IDENTITY_RECEIPT_SCHEMA)
    final_receipt = _load_receipt(final_receipt_path, FINAL_RECEIPT_SCHEMA)
    minimum_ratio = validate_locked_alignment_threshold(
        decision_receipt.get("minimum_alignment_ratio")
    )
    expected_ids, expected_id_errors = expected_secure_final_ids(
        decision_rows, candidate_by_clip, calibration_sha256, minimum_ratio
    )
    final_ids = {str(row.get("clip_id") or "") for row in final_rows}
    expected_ids_sha256 = SMR.sha256_json(sorted(expected_ids))
    by_source, ledger_errors = cumulative_verified_source_ledger(
        candidates, decision_rows, final_rows
    )
    if (
        candidate_receipt.get("status") != "AUDIT_REQUIRED"
        or candidate_receipt.get("manifest_sha256") != SMR.sha256_file(candidate_manifest_path)
        or int(candidate_receipt.get("candidate_clips") or -1) != len(candidates)
        or candidate_receipt.get("inventory_sha256") != SMR.sha256_file(Path(args.inventory))
        or decision_receipt.get("status") != "PASS"
        or decision_receipt.get("manifest_sha256") != SMR.sha256_file(decision_manifest_path)
        or decision_receipt.get("candidate_manifest_sha256") != SMR.sha256_file(candidate_manifest_path)
        or decision_receipt.get("candidate_receipt_sha256") != SMR.sha256_file(candidate_receipt_path)
        or decision_receipt.get("inventory_sha256") != SMR.sha256_file(Path(args.inventory))
        or not calibration_path.is_file()
        or decision_receipt.get("calibration_sha256") != SMR.sha256_file(calibration_path)
        or decision_receipt.get("identity_schema") != IDENTITY_SCHEMA
        or decision_receipt.get("scoring_contract_sha256")
        != identity_scoring_contract_sha256()
        or not math.isclose(
            float(args.minimum_ratio), minimum_ratio, rel_tol=0.0, abs_tol=1e-12
        )
        or final_receipt.get("status") != "PASS"
        or final_receipt.get("manifest_sha256") != SMR.sha256_file(final_manifest_path)
        or final_receipt.get("candidate_manifest_sha256") != SMR.sha256_file(candidate_manifest_path)
        or final_receipt.get("candidate_receipt_sha256") != SMR.sha256_file(candidate_receipt_path)
        or final_receipt.get("identity_manifest_sha256") != SMR.sha256_file(decision_manifest_path)
        or final_receipt.get("identity_receipt_sha256") != SMR.sha256_file(decision_receipt_path)
        or final_receipt.get("inventory_sha256") != SMR.sha256_file(Path(args.inventory))
        or final_receipt.get("calibration_sha256") != SMR.sha256_file(calibration_path)
        or final_receipt.get("identity_schema") != IDENTITY_SCHEMA
        or final_receipt.get("final_schema") != FINAL_SCHEMA
        or final_receipt.get("scoring_contract_sha256")
        != identity_scoring_contract_sha256()
        or float(final_receipt.get("minimum_alignment_ratio") or -1.0)
        != minimum_ratio
        or int(final_receipt.get("verified_final_clips", -1)) != len(final_rows)
        or expected_id_errors
        or final_ids != expected_ids
        or int(final_receipt.get("expected_final_clips", -1)) != len(expected_ids)
        or final_receipt.get("expected_final_ids_sha256") != expected_ids_sha256
        or final_receipt.get("final_id_set_complete") is not True
        or ledger_errors
        or final_receipt.get("cumulative_source_bindings_complete") is not True
        or final_receipt.get("by_source") != by_source
        or final_receipt.get("whole_final_audio_equals_identity_candidate_required") is not False
        or final_receipt.get("partial_final_audio_equals_identity_tested_run_required") is not False
        or final_receipt.get("final_window_audio_equals_identity_tested_window_required") is not True
        or final_receipt.get("independent_identity_window_finalization") is not True
        or final_receipt.get("final_audio_matches_source_frame_binding_required") is not True
        or final_receipt.get("canonical_source_overlap_requires_record_supersession") is not True
    ):
        raise IdentityRefused("cleanup receipts are missing, stale, or not chained end-to-end")
    decisions = {
        str(row.get("clip_id") or ""): row
        for row in decision_rows
    }
    finals = {
        str(row.get("clip_id") or ""): row
        for row in final_rows
    }
    selected = {value.strip() for value in args.source.split(",") if value.strip()}
    if selected:
        candidates = [
            row for row in candidates
            if str(row.get("source_family") or "") in selected
        ]
    inventory_expected_turns, inventory_source_packages = inventory_source_turn_requirements(
        SMR.read_jsonl(Path(args.inventory), required=True)
    )
    by_source: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for candidate in candidates:
        source = str(candidate.get("source_wav") or "")
        if source:
            by_source[source].append(candidate)
    rows = []
    removed = 0
    for source_value, source_candidates in sorted(by_source.items()):
        source = Path(source_value)
        first = source_candidates[0]
        transcript_path = Path(str(first.get("asr_transcript_receipt") or ""))
        try:
            transcript = _load_receipt(transcript_path, SMR.ASR_RECEIPT_SCHEMA)
            ready, reasons = retention_ready(
                transcript,
                source_candidates,
                decisions,
                finals,
                final_root=Path(args.final_root),
                minimum_ratio=minimum_ratio,
            )
            inventory_expected = inventory_expected_turns.get(str(source.resolve()), set())
            candidate_turns = {
                int(match.group(1))
                for row in source_candidates
                if (match := re.fullmatch(
                    r"sqlite:speeches:(\d+)", str(row.get("turn_id") or "")
                ))
            }
            if not inventory_expected or inventory_expected != candidate_turns:
                ready = False
                reasons.append("INVENTORY_WIDE_SOURCE_TURN_COVERAGE_INCOMPLETE")
            if not _within(source, Path(args.media_root)) or source.name.lower() != "source.wav":
                ready = False
                reasons.append("SOURCE_PATH_OUTSIDE_MEDIA_ROOT")
            if not source.is_file():
                ready = False
                reasons.append("SOURCE_WAV_MISSING")
            source_sha256 = SMR.sha256_file(source) if source.exists() else str(first.get("source_audio_sha256") or "")
            if source.exists() and source_sha256 != first.get("source_audio_sha256"):
                ready = False
                reasons.append("SOURCE_HASH_CHANGED")
            status = "READY" if ready else "RETAINED_UNRESOLVED"
            if ready and args.execute_cleanup and source.exists():
                source.unlink()
                status = "REMOVED_AFTER_SECURE_FINALIZATION"
                removed += 1
            rows.append({
                "schema": CLEANUP_SCHEMA,
                "status": status,
                "source_wav": source_value,
                "source_audio_sha256": source_sha256,
                "transcript_receipt": str(transcript_path.resolve()),
                "reference_url": transcript.get("reference_url"),
                "candidate_clips": [row["clip_id"] for row in source_candidates],
                "inventory_packages": sorted(
                    inventory_source_packages.get(str(source.resolve()), set())
                ),
                "reasons": reasons,
                "executed": bool(args.execute_cleanup),
                "checked_at": SMR.utc_now(),
            })
        except (IdentityRefused, OSError, ValueError, json.JSONDecodeError) as exc:
            rows.append({
                "schema": CLEANUP_SCHEMA,
                "status": "RETAINED_UNRESOLVED",
                "source_wav": source_value,
                "reasons": [str(exc)],
                "executed": bool(args.execute_cleanup),
                "checked_at": SMR.utc_now(),
            })
    SMR.write_jsonl_atomic(Path(args.cleanup_manifest), rows)
    receipt = {
        "schema": CLEANUP_RECEIPT_SCHEMA,
        "status": "PASS",
        "generated_at": SMR.utc_now(),
        "manifest": str(Path(args.cleanup_manifest).resolve()),
        "manifest_sha256": SMR.sha256_file(Path(args.cleanup_manifest)),
        "sources_checked": len(rows),
        "sources_ready": sum(row["status"] == "READY" for row in rows),
        "sources_removed": removed,
        "sources_retained": sum(row["status"] == "RETAINED_UNRESOLVED" for row in rows),
        "removal_requires_all_candidates_secure": True,
    }
    SMR.write_json_atomic(Path(args.cleanup_receipt), receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


def _load_receipt(path: Path, schema: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise IdentityRefused(f"receipt schema mismatch: {path}")
    return value


def run_manifest(args: argparse.Namespace) -> int:
    rows, summary = build_anchor_manifest(
        Path(args.verify), Path(args.audio_root), hash_audio=not args.no_hash_audio
    )
    receipt = persist_anchor_manifest(rows, summary, Path(args.anchor_manifest), Path(args.anchor_receipt))
    print(json.dumps(receipt, sort_keys=True))
    return 0


def _production_backend(args: argparse.Namespace) -> Any:
    try:
        return get_backend(args.backend, args.model_dir, args.model_manifest)
    except BackendRefused as exc:
        raise IdentityRefused(str(exc)) from exc


def run_embed(args: argparse.Namespace) -> int:
    rows = SMR.read_jsonl(Path(args.anchor_manifest), required=True)
    receipt = _load_receipt(Path(args.anchor_receipt), ANCHOR_RECEIPT_SCHEMA)
    if receipt.get("manifest_sha256") != SMR.sha256_file(Path(args.anchor_manifest)):
        raise IdentityRefused("anchor manifest receipt hash mismatch")
    backend = _production_backend(args)
    result = build_embedding_vault(
        rows,
        backend,
        Path(args.vault_root),
        max_anchors=args.max_anchors,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
        workers=args.workers,
        backend_factory=lambda: _production_backend(args),
    )
    receipt.update(result)
    model_manifest = Path(args.model_manifest)
    if not model_manifest.is_file():
        raise IdentityRefused("speaker model manifest is missing")
    receipt["model_manifest"] = str(model_manifest.resolve())
    receipt["model_manifest_sha256"] = SMR.sha256_file(model_manifest)
    receipt["primary_event_ids_unique"] = True
    receipt["primary_audio_hashes_unique"] = True
    receipt["generated_at"] = SMR.utc_now()
    SMR.write_json_atomic(Path(args.anchor_receipt), receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if result["status"] == "PASS" else 3


def run_calibrate(args: argparse.Namespace) -> int:
    receipt = _load_receipt(Path(args.anchor_receipt), ANCHOR_RECEIPT_SCHEMA)
    if receipt.get("status") != "PASS" or not receipt.get("combined_vectors"):
        raise IdentityRefused("complete anchor embeddings are required before calibration")
    vault_path = Path(str(receipt["combined_vectors"]))
    if receipt.get("combined_vectors_sha256") != SMR.sha256_file(vault_path):
        raise IdentityRefused("combined anchor vault hash mismatch")
    window_manifest = Path(str(receipt.get("calibration_window_manifest") or ""))
    if (
        receipt.get("calibration_protocol")
        != "held-out-event-candidate-domain-unanimous-best-anchor-v2"
        or not window_manifest.is_file()
        or receipt.get("calibration_window_manifest_sha256")
        != SMR.sha256_file(window_manifest)
        or int(receipt.get("calibration_windows_embedded") or -1)
        != int(receipt.get("calibration_windows_total") or -2)
        or not (
            receipt.get("primary_event_ids_unique") is True
            or receipt.get("same_event_anchor_windows_excluded_together") is True
        )
    ):
        raise IdentityRefused("candidate-domain calibration-window artifact is stale or incomplete")
    vault = load_vault(vault_path)
    calibration = calibrate_zero_false_accept(
        vault["vectors"],
        vault["members"].tolist(),
        set(receipt.get("eligible_targets") or []),
        event_ids=vault["event_ids"].tolist(),
        calibration_vectors=vault["calibration_vectors"],
        calibration_members=vault["calibration_members"].tolist(),
        calibration_event_ids=vault["calibration_event_ids"].tolist(),
        calibration_window_indexes=vault["calibration_window_indexes"].tolist(),
    )
    calibration.update({
        "generated_at": SMR.utc_now(),
        "anchor_manifest_sha256": receipt["manifest_sha256"],
        "anchor_vectors_sha256": receipt["combined_vectors_sha256"],
        "calibration_window_manifest": str(window_manifest.resolve()),
        "calibration_window_manifest_sha256": receipt["calibration_window_manifest_sha256"],
        "backend": receipt["backend"],
        "model_manifest_sha256": receipt["model_manifest_sha256"],
        "vaulted_speakers": receipt["vaulted_speakers"],
        "eligible_target_speakers": receipt["eligible_target_speakers"],
    })
    SMR.write_json_atomic(Path(args.calibration), calibration)
    print(json.dumps(calibration, sort_keys=True))
    return 0 if calibration["status"] == "PASS_ZERO_FALSE_ACCEPT_CALIBRATION" else 4


def run_status(args: argparse.Namespace) -> int:
    output = {
        "source_order": list(SOURCE_PRIORITY),
        "anchor_receipt": (
            json.loads(Path(args.anchor_receipt).read_text(encoding="utf-8-sig"))
            if Path(args.anchor_receipt).exists() else None
        ),
        "calibration": (
            json.loads(Path(args.calibration).read_text(encoding="utf-8-sig"))
            if Path(args.calibration).exists() else None
        ),
        "decisions": len(SMR.read_jsonl(Path(args.decision_manifest))),
        "final_clips": len(SMR.read_jsonl(Path(args.final_manifest))),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=(
            "manifest", "embed", "calibrate", "identify", "merge",
            "finalize", "cleanup", "status",
        )
    )
    parser.add_argument("--verify", default=str(VERIFY_PATH))
    parser.add_argument("--audio-root", default=str(AUDIO_ROOT))
    parser.add_argument("--anchor-manifest", default=str(ANCHOR_MANIFEST))
    parser.add_argument("--anchor-receipt", default=str(ANCHOR_RECEIPT))
    parser.add_argument("--vault-root", default=str(DEFAULT_VAULT_ROOT))
    parser.add_argument("--calibration", default=str(CALIBRATION_PATH))
    parser.add_argument("--backend", choices=("ecapa", "wav2vec2", "spectral-test"), default="ecapa")
    parser.add_argument("--model-dir", default=str(DEFAULT_ECAPA_MODEL))
    parser.add_argument("--model-manifest", default=str(DEFAULT_ECAPA_MANIFEST))
    parser.add_argument("--max-anchors", type=int, default=0)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--no-hash-audio", action="store_true")
    parser.add_argument("--decision-manifest", default=str(DECISION_MANIFEST))
    parser.add_argument("--decision-receipt", default=str(DECISION_RECEIPT))
    parser.add_argument("--merge-candidate-receipt", action="append", default=[])
    parser.add_argument("--merge-identity-receipt", action="append", default=[])
    parser.add_argument(
        "--allow-source-overlap", action="store_true",
        help=(
            "permit repeated source families only when candidate IDs, audio hashes, "
            "and exact source-frame ranges remain disjoint; intended for consolidating "
            "successive isolated batches before a source-disjoint top-level merge"
        ),
    )
    parser.add_argument(
        "--exclude-same-member-overlap", action="store_true",
        help=(
            "during an explicitly allowed source-family overlap merge, exclude a later "
            "candidate only when its source-frame interval overlaps an already included "
            "candidate for the same member; the exclusion is persisted in both receipts"
        ),
    )
    parser.add_argument("--final-manifest", default=str(FINAL_MANIFEST))
    parser.add_argument("--final-receipt", default=str(FINAL_RECEIPT))
    parser.add_argument("--candidate-manifest", default=str(DEFAULT_MEDIA_ROOT / "candidate_clips.jsonl"))
    parser.add_argument("--candidate-receipt", default=str(DEFAULT_MEDIA_ROOT / "candidate_clips.receipt.json"))
    parser.add_argument("--inventory", default=str(SMR.INVENTORY_PATH))
    parser.add_argument("--candidate-cache", default=str(DEFAULT_VAULT_ROOT / "candidate_embeddings"))
    parser.add_argument("--comparison-root", default=str(DEFAULT_MEDIA_ROOT / "identity_comparisons_v2"))
    parser.add_argument("--final-root", default=str(DEFAULT_FINAL_ROOT))
    parser.add_argument("--cleanup-manifest", default=str(CLEANUP_MANIFEST))
    parser.add_argument("--cleanup-receipt", default=str(CLEANUP_RECEIPT))
    parser.add_argument("--media-root", default=str(DEFAULT_MEDIA_ROOT))
    parser.add_argument("--source", default="")
    parser.add_argument("--include-remaining", action="store_true")
    parser.add_argument("--max-candidates", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--checkpoint-every", type=int, default=12)
    parser.add_argument("--window-sec", type=float, default=8.0)
    parser.add_argument("--minimum-ratio", type=float, default=0.68)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--execute-cleanup", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.action == "manifest":
        return run_manifest(args)
    if args.action == "embed":
        return run_embed(args)
    if args.action == "calibrate":
        return run_calibrate(args)
    if args.action == "identify":
        return run_identify(args)
    if args.action == "merge":
        return run_merge(args)
    if args.action == "finalize":
        return run_finalize(args)
    if args.action == "cleanup":
        return run_cleanup(args)
    return run_status(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IdentityRefused, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR {exc}", file=__import__("sys").stderr)
        raise SystemExit(2)
