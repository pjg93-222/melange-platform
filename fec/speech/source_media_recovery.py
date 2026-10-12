"""Inventory, discover, acquire, and bind source media for interview transcripts.

This is a deliberately fail-closed recovery lane.  ``fetch_interviews.py``
stored attributed transcript turns, not recordings.  A page-level player or a
title/date search result is therefore only a *candidate*.  No member clip is
emitted until the candidate audio contains the stored turn in transcript order.

The script has four resumable stages:

``inventory``
    Snapshot every distinct INTERVIEW package and its exact SQLite rowids.
``discover``
    Resolve official provider/media IDs with source-specific adapters.
``acquire``
    Download candidate audio into the off-repo media store without overwriting.
``align``
    ASR the recording, align stored turns monotonically, cut WAV clips, measure
    eGeMAPS, and emit a cryptographically bound candidate manifest.

The output of this module is supplemental and candidate-only.  Promotion into
the accepted speech corpus is a separate audited action; this module never
modifies ``C:\\melange-data\\member_clips\\clips_manifest.jsonl``.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import datetime as dt
import difflib
import email.utils
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
from typing import Any, Callable, Iterable, Iterator, Sequence
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import wave
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import ROOT as MB_ROOT  # noqa: E402


ROOT = Path(MB_ROOT)
DB_PATH = ROOT / "social_media.db"
INVENTORY_PATH = ROOT / "data" / "speech" / "source_media_inventory_v1.jsonl"
SUMMARY_PATH = ROOT / "data" / "speech" / "source_media_inventory_v1.summary.json"
RECEIPT_PATH = ROOT / "data" / "speech" / "source_media_inventory_v1.receipt.json"
DEFAULT_MEDIA_ROOT = Path(r"C:\melange-data\source_audio")
DEFAULT_MODEL = Path(r"C:\melange-data\models\faster-whisper-small")
CANONICAL_MANIFEST = Path(r"C:\melange-data\member_clips\clips_manifest.jsonl")
NPR_FETCH_LOG = Path(r"C:\melange-data\npr_audio\fetch_log.jsonl")
AXE_MANIFEST = Path(r"C:\melange-data\axe_audio\axe_manifest.jsonl")
AXE_FETCH_LOG = Path(r"C:\melange-data\axe_audio\fetch_log.jsonl")
SOTU_TARGETS = Path(r"C:\melange-data\sunday_audio\targets.json")
SOTU_FETCH_LOG = Path(r"C:\melange-data\sunday_audio\fetch_log.jsonl")
SOTU_AUDIO_ROOT = Path(r"C:\melange-data\sunday_audio")
LOCAL_REUSE_ASR_ROOT = Path(r"C:\melange-data\source_audio_cnn_local_reuse_asr")
LOCAL_REUSE_ALIGNED_INVENTORY = (
    ROOT / "data" / "speech" / "source_media_inventory_cnn_local_reuse_aligned.jsonl"
)
DEFAULT_ROSTER = ROOT / "data" / "engine" / "congestion.json"
SUPPLEMENT_MANIFEST = ROOT / "data" / "speech" / "source_media_supplement_v1.jsonl"
SUPPLEMENT_RECEIPT = ROOT / "data" / "speech" / "source_media_supplement_v1.receipt.json"
LOCAL_REUSE_MANIFEST = ROOT / "data" / "speech" / "source_media_local_reuse_cnn_v1.jsonl"
LOCAL_REUSE_RECEIPT = ROOT / "data" / "speech" / "source_media_local_reuse_cnn_v1.receipt.json"
DEFAULT_MEMBER_GAP = ROOT / "data" / "speech" / "member_speech_profile_gap_v1.jsonl"

SCHEMA = "speech-source-media-inventory-v1"
CLIP_SCHEMA = "speech-source-media-candidate-clip-v2"
CANDIDATE_RECEIPT_SCHEMA = "speech-source-media-candidate-clip-receipt-v2"
SUPPLEMENT_SCHEMA = "speech-source-media-supplement-clip-v1"
LOCAL_REUSE_SCHEMA = "speech-source-media-local-reuse-route-v1"
LOCAL_REUSE_RECEIPT_SCHEMA = "speech-source-media-local-reuse-route-receipt-v1"
UA = {
    "User-Agent": "MelangeSystemsResearchCollector/1.0 (public-records research)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TERMINAL_DISCOVERY = {
    "prepared",
    "exact_timed_captions",
    "exact_episode_asr_required",
    "partial_clip_asr_required",
    "candidate_asr_required",
    "blocked_no_exact_media",
    "blocked_no_public_media_id",
    "no_media",
    "ambiguous",
}
RETRYABLE_DISCOVERY = {"needs_probe", "probe_error"}
# Wall-clock ceilings for the two external binaries. Without them a stalled HTTPS/HLS read
# blocks a worker thread forever, and run_acquire/run_align both end in
# executor.shutdown(wait=True), so one hung child hangs the whole process with no output.
# houselive_collect.py:256 and discover_gap_youtube_turns.py:193 already bound theirs.
YT_DLP_SEARCH_TIMEOUT_SEC = 300
YT_DLP_DOWNLOAD_TIMEOUT_SEC = 1800
FFMPEG_TIMEOUT_SEC = 3600
ACQUIRABLE = {
    "exact_timed_captions",
    "exact_episode_asr_required",
    "partial_clip_asr_required",
    "candidate_asr_required",
}
OFFICIAL_YOUTUBE_CHANNELS = {
    "cnn": {"UCupvZG-5ko_eiXAupbDfxWw"},
    "cfr": {"UCL_A4jkwvKuMyToAPy3FQKQ"},
    "csis": set(),
    "aspen": set(),
}
CNN_SHOW_LABELS = {
    "ac360": "anderson cooper 360",
    "acd": "anderson cooper 360",
    "cnr": "cnn newsroom",
    "ctmo": "cnn this morning",
    "ebo": "erin burnett outfront",
    "sitroom": "the situation room",
    "sotu": "state of the union",
}

# Windows occasionally selects a reset-prone IPv6 route for CNN and a few feed
# hosts.  Preserve normal resolver fallback while preferring IPv4, matching the
# proven interview collector transport behavior.
_SYSTEM_GETADDRINFO = socket.getaddrinfo


def _ipv4_first(host: str, port: Any, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0):
    rows = _SYSTEM_GETADDRINFO(host, port, family, type, proto, flags)
    return sorted(rows, key=lambda row: row[0] != socket.AF_INET)


socket.getaddrinfo = _ipv4_first


class RecoveryError(RuntimeError):
    """An input or binding failed a fail-closed recovery check."""


class ConcurrentMemo:
    """Small single-flight LRU for immutable discovery responses.

    Only successful computations are retained. Concurrent requests for the
    same exact key share one computation; failures reach all current waiters
    but are not cached, so a later package can retry normally.
    """

    def __init__(self, max_entries: int = 64):
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.Lock()
        self._values: collections.OrderedDict[str, Any] = collections.OrderedDict()
        self._pending: dict[str, concurrent.futures.Future[Any]] = {}

    def get_or_compute(self, key: str, compute: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._values:
                value = self._values.pop(key)
                self._values[key] = value
                return value
            future = self._pending.get(key)
            leader = future is None
            if leader:
                future = concurrent.futures.Future()
                self._pending[key] = future
        assert future is not None
        if not leader:
            return future.result()
        try:
            value = compute()
        except BaseException as exc:
            future.set_exception(exc)
            with self._lock:
                self._pending.pop(key, None)
            raise
        with self._lock:
            self._values[key] = value
            self._values.move_to_end(key)
            while len(self._values) > self.max_entries:
                self._values.popitem(last=False)
            self._pending.pop(key, None)
        future.set_result(value)
        return value


def memoized_discovery(
    shared: dict[str, Any] | None, cache_name: str, key: str,
    compute: Callable[[], Any],
) -> Any:
    cache = (shared or {}).get(cache_name)
    return cache.get_or_compute(key, compute) if isinstance(cache, ConcurrentMemo) else compute()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path, block: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(block)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def seconds_to_frame(seconds: float, sample_rate: int) -> int:
    """Convert timestamp geometry without truncating an exact sample boundary."""
    return int(round(float(seconds) * int(sample_rate)))


def wav_pcm_slice_sha256(
    path: Path, start_frame: int, frame_count: int, *, sample_rate: int = 16000
) -> str:
    """Hash exact mono PCM16 frames without a float or timestamp round-trip."""
    if start_frame < 0 or frame_count <= 0:
        raise RecoveryError("invalid WAV PCM slice bounds")
    try:
        with wave.open(str(path), "rb") as handle:
            contract = (
                handle.getnchannels(), handle.getsampwidth(), handle.getframerate()
            )
            if contract != (1, 2, sample_rate):
                raise RecoveryError(
                    f"WAV PCM contract mismatch: expected mono PCM16/{sample_rate}, got {contract}"
                )
            if start_frame + frame_count > handle.getnframes():
                raise RecoveryError("WAV PCM slice exceeds secured source frames")
            handle.setpos(start_frame)
            payload = handle.readframes(frame_count)
    except (OSError, wave.Error) as exc:
        raise RecoveryError(f"invalid WAV PCM source: {path}") from exc
    if len(payload) != frame_count * 2:
        raise RecoveryError("WAV PCM slice byte count mismatch")
    return sha256_bytes(payload)


def wav_full_facts(path: Path, *, sample_rate: int = 16000) -> dict[str, Any]:
    """Hash one secured WAV and its complete PCM payload under the lane contract."""
    if not path.is_absolute() or not path.is_file():
        raise RecoveryError(f"local reuse WAV is missing or non-absolute: {path}")
    pcm_digest = hashlib.sha256()
    pcm_bytes = 0
    try:
        with wave.open(str(path), "rb") as handle:
            contract = (
                handle.getnchannels(), handle.getsampwidth(), handle.getframerate()
            )
            if contract != (1, 2, sample_rate) or handle.getcomptype() != "NONE":
                raise RecoveryError(
                    f"WAV PCM contract mismatch: expected mono PCM16/{sample_rate}, got {contract}"
                )
            frame_count = int(handle.getnframes())
            if frame_count <= 0:
                raise RecoveryError("local reuse WAV has no PCM frames")
            while True:
                payload = handle.readframes(1024 * 1024)
                if not payload:
                    break
                pcm_digest.update(payload)
                pcm_bytes += len(payload)
    except (OSError, wave.Error) as exc:
        raise RecoveryError(f"invalid local reuse WAV: {path}") from exc
    if pcm_bytes != frame_count * 2:
        raise RecoveryError("local reuse WAV PCM byte count does not match its frame count")
    return {
        "wav": str(path.resolve()),
        "bytes": path.stat().st_size,
        "source_audio_sha256": sha256_file(path),
        "source_pcm_sha256": pcm_digest.hexdigest(),
        "sample_rate": sample_rate,
        "channels": 1,
        "sample_width_bytes": 2,
        "frame_count": frame_count,
        "duration_sec": round(frame_count / float(sample_rate), 6),
    }


def _read_json_array(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"invalid or missing JSON array: {path}") from exc
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise RecoveryError(f"JSON root is not an object array: {path}")
    return value


def _require_public_reference(value: Any) -> str:
    reference = str(value or "").strip()
    parsed = urllib.parse.urlsplit(reference)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RecoveryError(f"local reuse source lacks a public reference URL: {reference!r}")
    return reference


def _route_row_hash(row: dict[str, Any]) -> str:
    body = dict(row)
    body.pop("route_row_sha256", None)
    return sha256_json(body)


def _episode_guid(package: str) -> str:
    parsed = urllib.parse.urlsplit(package)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[-2].lower() != "episodes" or not parts[-1]:
        raise RecoveryError(f"Axe package has no exact episode GUID path component: {package}")
    return parts[-1].lower()


def _success_log_row(
    rows: Sequence[dict[str, Any]], *, key: str, value: str, status: str,
    wav: Path | None = None,
) -> dict[str, Any]:
    matches = [
        row for row in rows
        if str(row.get(key) or "").lower() == value.lower()
        and str(row.get("status") or "").lower() == status.lower()
    ]
    if wav is not None:
        wav_matches = [
            row for row in matches
            if row.get("wav")
            and os.path.normcase(str(Path(str(row["wav"])).resolve()))
            == os.path.normcase(str(wav.resolve()))
        ]
        matches = wav_matches
    if len(matches) != 1:
        raise RecoveryError(
            f"expected one {status!r} collection log for {key}={value!r}; found {len(matches)}"
        )
    return matches[0]


def _local_reuse_source_id(kind: str, source_sha256: str, discriminator: str) -> str:
    digest = sha256_text("|".join((kind, source_sha256, discriminator)))[:24]
    return f"cnn-local-{kind}-{digest}"


def _local_reuse_asr_paths(asr_root: Path, source_id: str) -> tuple[Path, Path]:
    if not asr_root.is_absolute():
        raise RecoveryError("local reuse ASR root must be absolute")
    directory = asr_root / source_id
    return directory / "transcript.words.jsonl", directory / "transcript.receipt.json"


def _inventory_route(
    record: dict[str, Any], *, basis: str, route_key: dict[str, Any]
) -> dict[str, Any]:
    package = str(record.get("package") or "")
    package_key = str(record.get("package_id") or "")
    if (
        record.get("schema") != SCHEMA
        or not package
        or package_key != package_id(package)
        or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("inventory_sha256") or ""))
        or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("transcript_sha256") or ""))
    ):
        raise RecoveryError(f"inventory route is stale or malformed: {package or package_key}")
    return {
        "package_id": package_key,
        "package": package,
        "source_family": str(record.get("source_family") or ""),
        "inventory_row_sha256": record["inventory_sha256"],
        "crawler_transcript_sha256": record["transcript_sha256"],
        "turn_count": int(record.get("turn_count") or 0),
        "word_count": int(record.get("word_count") or 0),
        "localization_routing_basis": basis,
        "localization_route_key": route_key,
        "speaker_identity_authority": False,
        "identity_status": "NOT_EVALUATED",
    }


def build_cnn_local_reuse_routes(
    inventory: Sequence[dict[str, Any]],
    sotu_targets: Sequence[dict[str, Any]],
    sotu_fetch_log: Sequence[dict[str, Any]],
    axe_manifest: Sequence[dict[str, Any]],
    axe_fetch_log: Sequence[dict[str, Any]],
    *,
    sotu_root: Path = SOTU_AUDIO_ROOT,
    asr_root: Path = LOCAL_REUSE_ASR_ROOT,
    input_hashes: dict[str, str] | None = None,
    expected_sotu_sources: int = 33,
    expected_sotu_routes: int = 37,
    expected_axe_sources: int = 5,
    expected_axe_inventory_packages: int = 6,
    facts_loader: Callable[[Path], dict[str, Any]] = wav_full_facts,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Route secured CNN holdings for transcript localization, never identity.

    SOTU date/show and Axe episode GUID are discovery keys only.  Speaker names,
    titles, and airtimes from the collection manifests are deliberately not
    copied into this manifest and cannot affect a later identity decision.
    """
    hashes = dict(input_hashes or {})
    sotu = [row for row in sotu_targets if str(row.get("fam") or "").lower() == "sotu"]
    if len(sotu) != expected_sotu_sources:
        raise RecoveryError(
            f"expected {expected_sotu_sources} SOTU physical sources; found {len(sotu)}"
        )
    target_dates = [str(row.get("date") or "") for row in sotu]
    if any(not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in target_dates):
        raise RecoveryError("SOTU target has an invalid date localization key")
    if len(set(target_dates)) != len(target_dates):
        raise RecoveryError("SOTU target dates are ambiguous or duplicated")

    selected_sotu_inventory = [
        row for row in inventory
        if row.get("source_family") == "cnn"
        and str(row.get("show") or "").lower() == "sotu"
        and str(row.get("date") or "") in set(target_dates)
    ]
    if len(selected_sotu_inventory) != expected_sotu_routes:
        raise RecoveryError(
            f"expected {expected_sotu_routes} current SOTU package routes; "
            f"found {len(selected_sotu_inventory)}"
        )
    sotu_by_date: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for record in selected_sotu_inventory:
        sotu_by_date[str(record.get("date") or "")].append(record)

    axe_inventory = [row for row in inventory if row.get("source_family") == "cnn-axe-files"]
    if len(axe_inventory) != expected_axe_inventory_packages:
        raise RecoveryError(
            f"expected {expected_axe_inventory_packages} current Axe packages; "
            f"found {len(axe_inventory)}"
        )
    selected_package_ids = [
        str(row.get("package_id") or "")
        for row in selected_sotu_inventory + axe_inventory
    ]
    if len(set(selected_package_ids)) != len(selected_package_ids):
        raise RecoveryError("current inventory contains a duplicate local reuse package route")

    manifest_by_guid: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in axe_manifest:
        guid = str(row.get("guid") or "").strip().lower()
        if guid:
            manifest_by_guid[guid].append(row)

    routes: list[dict[str, Any]] = []
    routed_package_ids: set[str] = set()
    routed_wavs: set[str] = set()

    def add_source(
        *, kind: str, discriminator: str, source_facts: dict[str, Any],
        reference_url: str, package_routes: list[dict[str, Any]],
        collection_provenance: dict[str, Any],
    ) -> None:
        wav_key = os.path.normcase(str(Path(str(source_facts.get("wav") or "")).resolve()))
        if wav_key in routed_wavs:
            raise RecoveryError("one physical WAV was routed as more than one local reuse source")
        routed_wavs.add(wav_key)
        for route in package_routes:
            package_key = str(route.get("package_id") or "")
            if package_key in routed_package_ids:
                raise RecoveryError(f"package was routed more than once: {package_key}")
            routed_package_ids.add(package_key)
        source_id = _local_reuse_source_id(
            kind, str(source_facts["source_audio_sha256"]), discriminator
        )
        words_path, asr_receipt_path = _local_reuse_asr_paths(asr_root, source_id)
        row = {
            "schema": LOCAL_REUSE_SCHEMA,
            "status": "LOCALIZATION_ONLY_ASR_REQUIRED",
            "physical_source_id": source_id,
            "source_kind": kind,
            "source_discriminator": discriminator,
            "reference_url": _require_public_reference(reference_url),
            "source": source_facts,
            "collection_provenance": collection_provenance,
            "asr": {
                "status": "REQUIRED",
                "transcript": str(words_path.resolve()),
                "receipt": str(asr_receipt_path.resolve()),
                "one_receipt_for_physical_source": True,
                "package_route_count": len(package_routes),
                "speaker_identity_authority": False,
            },
            "package_routes": sorted(package_routes, key=lambda item: item["package"]),
            "safety": {
                "candidate_localization_only": True,
                "speaker_identity_authority": False,
                "date_show_guid_title_member_airtime_identity_authority": False,
                "transcript_audio_alignment_required": True,
                "exhaustive_voice_consensus_required_before_identity": True,
                "identity_accepted": False,
                "promotable": False,
                "cleanup_authorized": False,
                "moneyball_modified": False,
            },
        }
        row["route_row_sha256"] = _route_row_hash(row)
        routes.append(row)

    for target in sorted(sotu, key=lambda row: str(row.get("date") or "")):
        date = str(target["date"])
        records = sotu_by_date.get(date) or []
        if not records:
            raise RecoveryError(f"SOTU source has no current package route: {date}")
        wav = (sotu_root / f"sotu_{date}.wav").resolve()
        log = _success_log_row(
            sotu_fetch_log, key="sid", value=f"sotu_{date}", status="OK"
        )
        facts = facts_loader(wav)
        if int(log.get("bytes") or -1) != int(facts.get("bytes") or -2):
            raise RecoveryError(f"SOTU fetch byte receipt does not match WAV: {date}")
        package_routes = [
            _inventory_route(
                record,
                basis="candidate-localization:date+show",
                route_key={"date": date, "show": "sotu"},
            )
            for record in records
        ]
        add_source(
            kind="sotu-podcast",
            discriminator=date,
            source_facts=facts,
            reference_url=str(target.get("url") or ""),
            package_routes=package_routes,
            collection_provenance={
                "collector_manifest_sha256": hashes.get("sotu_targets", ""),
                "collector_fetch_log_sha256": hashes.get("sotu_fetch_log", ""),
                "collector_source_id": f"sotu_{date}",
                "collector_fetch_status": "OK",
                "inventory_file_sha256": hashes.get("inventory", ""),
            },
        )

    unresolved: list[dict[str, str]] = []
    axe_sources = 0
    for record in sorted(axe_inventory, key=lambda row: str(row.get("package") or "")):
        guid = _episode_guid(str(record.get("package") or ""))
        matches = manifest_by_guid.get(guid) or []
        if not matches:
            unresolved.append({
                "package_id": str(record.get("package_id") or ""),
                "package": str(record.get("package") or ""),
                "episode_guid": guid,
                "status": "UNRESOLVED_NO_EXACT_LOCAL_GUID",
            })
            continue
        if len(matches) != 1:
            raise RecoveryError(f"Axe episode GUID is ambiguous or duplicated: {guid}")
        matched = matches[0]
        wav = Path(str(matched.get("wav") or ""))
        log = _success_log_row(
            axe_fetch_log, key="guid", value=guid, status="downloaded", wav=wav
        )
        facts = facts_loader(wav.resolve())
        package_route = _inventory_route(
            record,
            basis="candidate-localization:exact-episode-guid",
            route_key={"episode_guid": guid},
        )
        add_source(
            kind="axe-files-podcast",
            discriminator=guid,
            source_facts=facts,
            reference_url=str(record.get("package") or ""),
            package_routes=[package_route],
            collection_provenance={
                "collector_manifest_sha256": hashes.get("axe_manifest", ""),
                "collector_fetch_log_sha256": hashes.get("axe_fetch_log", ""),
                "collector_episode_guid": guid,
                "collector_fetch_status": str(log.get("status") or ""),
                "inventory_file_sha256": hashes.get("inventory", ""),
            },
        )
        axe_sources += 1

    if axe_sources != expected_axe_sources or len(unresolved) != (
        expected_axe_inventory_packages - expected_axe_sources
    ):
        raise RecoveryError(
            f"expected {expected_axe_sources} exact local Axe GUID routes and "
            f"{expected_axe_inventory_packages - expected_axe_sources} unresolved; "
            f"found {axe_sources} and {len(unresolved)}"
        )
    expected_sources = expected_sotu_sources + expected_axe_sources
    expected_routes = expected_sotu_routes + expected_axe_sources
    validate_cnn_local_reuse_routes(
        routes,
        expected_sources=expected_sources,
        expected_package_routes=expected_routes,
        require_asr=False,
    )
    return sorted(routes, key=lambda row: row["physical_source_id"]), unresolved


def validate_cnn_local_reuse_routes(
    rows: Sequence[dict[str, Any]], *, expected_sources: int = 38,
    expected_package_routes: int = 42, require_asr: bool = False,
) -> dict[str, int]:
    if len(rows) != expected_sources:
        raise RecoveryError(
            f"expected {expected_sources} local reuse sources; found {len(rows)}"
        )
    source_ids: set[str] = set()
    wavs: set[str] = set()
    source_hashes: set[str] = set()
    package_ids: set[str] = set()
    asr_receipts: set[str] = set()
    asr_ready = 0
    for row in rows:
        if row.get("schema") != LOCAL_REUSE_SCHEMA:
            raise RecoveryError("local reuse manifest schema mismatch")
        if row.get("route_row_sha256") != _route_row_hash(row):
            raise RecoveryError("local reuse route row hash mismatch")
        safety = row.get("safety") or {}
        if not (
            safety.get("candidate_localization_only") is True
            and safety.get("speaker_identity_authority") is False
            and safety.get("date_show_guid_title_member_airtime_identity_authority") is False
            and safety.get("identity_accepted") is False
            and safety.get("promotable") is False
            and safety.get("cleanup_authorized") is False
            and safety.get("moneyball_modified") is False
        ):
            raise RecoveryError("local reuse safety contract was weakened")
        source_id = str(row.get("physical_source_id") or "")
        source = row.get("source") or {}
        wav_path = Path(str(source.get("wav") or ""))
        wav = os.path.normcase(str(wav_path.resolve()))
        source_hash = str(source.get("source_audio_sha256") or "")
        if (
            not source_id or source_id in source_ids
            or not wav_path.is_absolute()
            or not wav or wav in wavs
            or not re.fullmatch(r"[0-9a-f]{64}", source_hash)
            or source_hash in source_hashes
            or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("source_pcm_sha256") or ""))
            or int(source.get("sample_rate") or 0) != 16000
            or int(source.get("channels") or 0) != 1
            or int(source.get("sample_width_bytes") or 0) != 2
            or int(source.get("frame_count") or 0) <= 0
            or int(source.get("bytes") or 0) <= 44
            or abs(
                float(source.get("duration_sec") or 0)
                - int(source.get("frame_count") or 0) / 16000.0
            ) > 0.000001
        ):
            raise RecoveryError("local reuse physical source is missing or duplicated")
        source_ids.add(source_id)
        wavs.add(wav)
        source_hashes.add(source_hash)
        _require_public_reference(row.get("reference_url"))
        asr = row.get("asr") or {}
        transcript_path = Path(str(asr.get("transcript") or ""))
        receipt_path = Path(str(asr.get("receipt") or ""))
        receipt = os.path.normcase(str(receipt_path.resolve()))
        if (
            not receipt or receipt in asr_receipts
            or not transcript_path.is_absolute() or not receipt_path.is_absolute()
            or transcript_path == receipt_path or transcript_path.parent != receipt_path.parent
            or asr.get("one_receipt_for_physical_source") is not True
            or asr.get("speaker_identity_authority") is not False
        ):
            raise RecoveryError("local reuse ASR receipt is missing or shared across sources")
        asr_receipts.add(receipt)
        routes = row.get("package_routes") or []
        if int(asr.get("package_route_count") or -1) != len(routes) or not routes:
            raise RecoveryError("local reuse ASR/package route count mismatch")
        for route in routes:
            package_key = str(route.get("package_id") or "")
            if package_key in package_ids or route.get("speaker_identity_authority") is not False:
                raise RecoveryError("local reuse package route is duplicated or identity-authoritative")
            if route.get("identity_status") != "NOT_EVALUATED":
                raise RecoveryError("local reuse route claims a speaker identity decision")
            package_ids.add(package_key)
        if asr.get("status") == "PASS":
            words_path = transcript_path
            if (
                not words_path.is_file() or not receipt_path.is_file()
                or asr.get("transcript_sha256") != sha256_file(words_path)
                or asr.get("receipt_sha256") != sha256_file(receipt_path)
            ):
                raise RecoveryError("local reuse ASR artifacts are missing or hash-invalid")
            transcript_receipt = _read_json_object(receipt_path)
            transcript_rows = read_jsonl(words_path, required=True)
            word_count, normalized_count = _validate_asr_rows(transcript_rows)
            if (
                transcript_receipt.get("schema") != ASR_RECEIPT_SCHEMA
                or transcript_receipt.get("status") != "PASS"
                or transcript_receipt.get("speaker_identity_authority") is not False
                or transcript_receipt.get("source_audio_sha256") != source_hash
                or Path(str(transcript_receipt.get("transcript") or "")).resolve()
                != words_path.resolve()
                or transcript_receipt.get("transcript_sha256") != sha256_file(words_path)
                or int(transcript_receipt.get("word_count") or -1) != word_count
                or int(transcript_receipt.get("normalized_token_count") or -1)
                != normalized_count
                or asr.get("source_audio_sha256") != source_hash
                or int(asr.get("word_count") or -1) != word_count
                or int(asr.get("normalized_token_count") or -1) != normalized_count
                or row.get("status") != "ASR_READY_LOCALIZATION_ONLY"
            ):
                raise RecoveryError("local reuse ASR receipt contract is stale or unsafe")
            asr_ready += 1
        else:
            if asr.get("status") != "REQUIRED" or row.get("status") != "LOCALIZATION_ONLY_ASR_REQUIRED":
                raise RecoveryError("local reuse ASR pending status is malformed")
            if require_asr:
                raise RecoveryError("local reuse source is missing its required ASR receipt")
    if len(package_ids) != expected_package_routes:
        raise RecoveryError(
            f"expected {expected_package_routes} unique local reuse package routes; "
            f"found {len(package_ids)}"
        )
    return {
        "physical_sources": len(source_ids),
        "package_routes": len(package_ids),
        "asr_sources_ready": asr_ready,
        "asr_sources_pending": len(source_ids) - asr_ready,
    }


def staged_clip_key(
    source_sha256: str,
    start_frame: int,
    frame_count: int,
    sample_rate: int,
    turn_id: str,
    transcript_sha256: str,
) -> str:
    return sha256_text("|".join((
        source_sha256,
        str(start_frame),
        str(frame_count),
        str(sample_rate),
        turn_id,
        transcript_sha256,
    )))


def stable_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return sha256_bytes(stable_json(value))


def read_jsonl(path: Path, required: bool = False) -> list[dict[str, Any]]:
    if not path.exists():
        if required:
            raise RecoveryError(f"required JSONL is missing: {path}")
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RecoveryError(
                    f"invalid JSON at {path}:{line_number}: {exc.msg}"
                ) from exc
            if not isinstance(value, dict):
                raise RecoveryError(f"non-object JSON row at {path}:{line_number}")
            rows.append(value)
    return rows


def load_member_gap(path_value: str) -> dict[str, dict[str, Any]]:
    """Load the explicit per-member recovery target, fail-closed.

    An empty argument preserves the exhaustive inventory behavior. When a
    path is supplied, only rows marked ``target_recovery`` are eligible.
    """
    if not path_value:
        return {}
    path = Path(path_value)
    rows = read_jsonl(path, required=True)
    targets: dict[str, dict[str, Any]] = {}
    for row in rows:
        member = str(row.get("member") or "").strip()
        if not member or row.get("schema") != "speech-member-profile-gap-v1":
            raise RecoveryError(f"invalid member-gap row in {path}")
        recovery_ready = row.get("media_recovery_ready")
        if recovery_ready is True or (
            recovery_ready is None and row.get("target_recovery") is True
        ):
            targets[member] = row
    if not targets:
        raise RecoveryError(f"member-gap has no recovery targets: {path}")
    return targets


def member_gap_priority(
    row: dict[str, Any], targets: dict[str, dict[str, Any]]
) -> tuple[int, float, float, str] | None:
    """Return recovery priority or None when an inventory row cannot fill a gap."""
    if not targets:
        return (0, 0.0, 0.0, str(row.get("package_id") or ""))
    matched = [
        targets[str(item.get("member") or "")]
        for item in (row.get("members") or [])
        if str(item.get("member") or "") in targets
    ]
    if not matched:
        return None
    status_rank = min(
        0 if item.get("status") == "NO_NONFLOOR_AUDIO" else 1
        for item in matched
    )
    clips_needed = max(float(item.get("clips_needed") or 0.0) for item in matched)
    minutes_needed = max(float(item.get("minutes_needed") or 0.0) for item in matched)
    return (
        status_rank,
        -clips_needed,
        -minutes_needed,
        str(row.get("package_id") or ""),
    )


def replace_atomic_with_retry(temp: Path, target: Path, attempts: int = 60) -> None:
    """Tolerate short OneDrive/AV read locks without weakening atomic writes."""
    for attempt in range(attempts):
        try:
            os.replace(temp, target)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(0.05 * (attempt + 1), 0.5))


def write_jsonl_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        replace_atomic_with_retry(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        replace_atomic_with_retry(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def normalize_tokens(value: str) -> list[str]:
    value = unicodedata.normalize("NFKD", value.lower())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", value)


def normalized_title(value: str) -> str:
    return " ".join(normalize_tokens(html.unescape(value)))


def package_id(package: str) -> str:
    return "pkg:" + sha256_text(package.strip())[:24]


def source_family(package: str, show: str = "") -> str:
    host = (urllib.parse.urlsplit(package).hostname or "").lower()
    path = urllib.parse.urlsplit(package).path.lower()
    if host == "transcripts.cnn.com":
        return "cnn"
    if host in {"www.cnn.com", "cnn.com"} and "/axe-files/" in path:
        return "cnn-axe-files"
    if host.endswith("foxnews.com"):
        return "fox"
    if host in {"www.ms.now", "ms.now"}:
        return "msnow"
    if host.endswith("c-span.org"):
        return "cspan"
    if host.endswith("nbcnews.com"):
        return "nbc-mtp"
    if host.endswith("cbsnews.com"):
        return "cbs-ftn"
    if host.endswith("abcnews.go.com"):
        return "abc-this-week"
    if host.endswith("pbs.org"):
        return "pbs"
    if host.endswith("npr.org"):
        return "npr"
    if host.endswith("hughhewitt.com"):
        return "hugh-hewitt"
    if host.endswith("cfr.org"):
        return "cfr"
    if host.endswith("csis.org"):
        return "csis"
    if host.endswith("aspensecurityforum.org"):
        return "aspen"
    return host or normalized_title(show) or "unknown"


def default_discovery(family: str, show: str) -> dict[str, Any]:
    if family == "cnn":
        return {
            "status": "needs_probe",
            "adapter": "cnn_official_media_search+asr",
            "reason": (
                "transcript page has show/date/segment/clock evidence but no direct media ID; "
                "search official CNN assets and admit only a strong ordered ASR match"
            ),
        }
    if family == "abc-this-week":
        return {
            "status": "blocked_no_exact_media",
            "adapter": family,
            "reason": "transcript-page media is absent or unrelated autoplay; separate official asset required",
        }
    if family == "fox" and "sunday" not in normalized_title(show):
        return {
            "status": "needs_probe",
            "adapter": "fox_inline_partial",
            "reason": "official page may expose a highlight; only aligned turns within that clip are admissible",
        }
    if family == "nbc-mtp":
        return {
            "status": "needs_probe",
            "adapter": "nbc_simplecast",
            "reason": "resolve official Meet the Press feed episode by exact date, then require ASR",
        }
    return {"status": "needs_probe", "adapter": family, "reason": "official media discovery pending"}


def load_existing_prepared() -> tuple[set[str], set[str], set[str]]:
    packages: set[str] = set()
    npr_packages: set[str] = set()
    axe_guids: set[str] = set()
    for row in read_jsonl(CANONICAL_MANIFEST):
        package = str(row.get("package") or "").strip()
        if package:
            packages.add(package)
    for row in read_jsonl(NPR_FETCH_LOG):
        if str(row.get("status") or "").upper() != "OK":
            continue
        package = str(row.get("pkg") or row.get("package") or row.get("url") or "").strip()
        if package:
            npr_packages.add(package)
    for row in read_jsonl(AXE_MANIFEST):
        guid = str(row.get("guid") or row.get("episode_guid") or "").strip().lower()
        if guid and row.get("wav"):
            axe_guids.add(guid)
    return packages, npr_packages, axe_guids


def snapshot_inventory(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    if not db_path.exists():
        raise RecoveryError(f"interview database is missing: {db_path}")
    prepared_packages, npr_packages, axe_guids = load_existing_prepared()
    uri = "file:" + db_path.resolve().as_posix() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            """
            SELECT rowid, member, date, chamber, package, nwords, text
            FROM speeches
            WHERE source='INTERVIEW' AND package IS NOT NULL AND trim(package) != ''
            ORDER BY package, rowid
            """
        ).fetchall()
    finally:
        con.close()
    grouped: dict[str, list[tuple[Any, ...]]] = collections.defaultdict(list)
    for row in rows:
        grouped[str(row[4]).strip()].append(row)
    output: list[dict[str, Any]] = []
    for package in sorted(grouped):
        turns = grouped[package]
        shows = sorted({str(row[3] or "").strip() for row in turns})
        dates = sorted({str(row[2] or "").strip() for row in turns if str(row[2] or "").strip()})
        family = source_family(package, shows[0] if shows else "")
        member_groups: dict[str, list[tuple[Any, ...]]] = collections.defaultdict(list)
        for row in turns:
            member_groups[str(row[1])].append(row)
        members = []
        for member in sorted(member_groups):
            member_rows = member_groups[member]
            members.append(
                {
                    "member": member,
                    "rowids": [int(row[0]) for row in member_rows],
                    "turns": len(member_rows),
                    "words": sum(int(row[5] or 0) for row in member_rows),
                    "text_sha256": sha256_json(
                        [[int(row[0]), str(row[6] or "")] for row in member_rows]
                    ),
                }
            )
        discovery = default_discovery(family, shows[0] if shows else "")
        guid_match = re.search(r"/episodes/([a-f0-9-]{20,})", package, re.I)
        already = package in prepared_packages
        if family == "npr" and package in npr_packages:
            already = True
        if family == "cnn-axe-files" and guid_match and guid_match.group(1).lower() in axe_guids:
            already = True
        if already:
            discovery = {
                "status": "prepared",
                "adapter": "existing_verified_local_corpus",
                "reason": "source media is already present in a local provenance-bearing corpus",
            }
        record = {
            "schema": SCHEMA,
            "package_id": package_id(package),
            "source_family": family,
            "source_host": (urllib.parse.urlsplit(package).hostname or "").lower(),
            "show": shows[0] if len(shows) == 1 else shows,
            "date": dates[0] if len(dates) == 1 else dates,
            "package": package,
            "turn_count": len(turns),
            "word_count": sum(int(row[5] or 0) for row in turns),
            "members": members,
            "transcript_sha256": sha256_json(
                [[int(row[0]), str(row[1]), str(row[6] or "")] for row in turns]
            ),
            "discovery": discovery,
            "inventory_at": utc_now(),
        }
        record["inventory_sha256"] = sha256_json(
            {key: value for key, value in record.items() if key != "inventory_sha256"}
        )
        output.append(record)
    return output


def fetch_bytes(url: str, timeout: int = 90, max_bytes: int | None = 32 * 1024 * 1024) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise RecoveryError(f"refusing non-HTTPS URL: {url}")
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        chunks: list[bytes] = []
        seen = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            seen += len(chunk)
            if max_bytes is not None and seen > max_bytes:
                raise RecoveryError(f"response exceeds {max_bytes} bytes: {url}")
            chunks.append(chunk)
    return b"".join(chunks)


def fetch_text(url: str, timeout: int = 90) -> str:
    return fetch_bytes(url, timeout=timeout).decode("utf-8", "replace")


def fetch_json(url: str, timeout: int = 90) -> Any:
    return json.loads(fetch_text(url, timeout=timeout))


def parse_feed(url: str) -> list[dict[str, Any]]:
    raw = fetch_bytes(url)
    root = ET.fromstring(raw)
    items = root.findall(".//item")
    output: list[dict[str, Any]] = []
    for item in items:
        enclosure = item.find("enclosure")
        pub = (item.findtext("pubDate") or "").strip()
        try:
            date = email.utils.parsedate_to_datetime(pub).date().isoformat() if pub else ""
        except (TypeError, ValueError):
            date = ""
        output.append(
            {
                "guid": (item.findtext("guid") or "").strip(),
                "title": html.unescape((item.findtext("title") or "").strip()),
                "description": html.unescape((item.findtext("description") or "").strip()),
                "date": date,
                "media_url": (enclosure.attrib.get("url", "").strip() if enclosure is not None else ""),
                "media_type": (enclosure.attrib.get("type", "").strip() if enclosure is not None else ""),
                "length": (enclosure.attrib.get("length", "").strip() if enclosure is not None else ""),
            }
        )
    return output


def youtube_ids(value: str) -> list[str]:
    patterns = [
        r"youtube(?:-nocookie)?\.com/(?:embed|live)/([A-Za-z0-9_-]{11})",
        r"youtube\.com/watch\?[^\"'<> ]*v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, html.unescape(value), re.I):
            if match.group(1) not in found:
                found.append(match.group(1))
    return found


def media_result(
    status: str,
    adapter: str,
    reason: str,
    *,
    provider: str | None = None,
    media_id: str | None = None,
    media_url: str | None = None,
    evidence: dict[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "adapter": adapter,
        "reason": reason,
        "probed_at": utc_now(),
    }
    if provider:
        result["provider"] = provider
    if media_id:
        result["media_id"] = media_id
    if media_url:
        result["media_url"] = media_url
    if evidence:
        result["evidence"] = evidence
    if candidates is not None:
        result["candidates"] = candidates
    return result


def discover_npr(record: dict[str, Any], page: str) -> dict[str, Any]:
    urls = re.findall(r"https://ondemand\.npr\.org/[^\"'<> ]+?\.mp3(?:\?[^\"'<> ]*)?", page, re.I)
    urls = list(dict.fromkeys(html.unescape(url) for url in urls))
    if len(urls) == 1:
        clean = urls[0].split("?", 1)[0]
        sid = record["package"].rstrip("/").rsplit("/", 1)[-1]
        return media_result(
            "exact_episode_asr_required",
            "npr_download_link",
            "official NPR transcript page exposes one download asset",
            provider="npr-ondemand",
            media_id=f"npr:{sid}:{sha256_text(clean)[:12]}",
            media_url=urls[0],
            evidence={"canonical_media_url": clean, "asset_count": 1},
        )
    return media_result(
        "no_media" if not urls else "ambiguous",
        "npr_download_link",
        "transcript page exposes no audio asset" if not urls else "transcript page exposes multiple audio assets",
        evidence={"asset_count": len(urls)},
    )


def discover_pbs(record: dict[str, Any], page: str) -> dict[str, Any]:
    ids = list(dict.fromkeys(re.findall(r"partnerplayer/(\d{6,})", page, re.I)))
    if len(ids) != 1:
        return media_result(
            "no_media" if not ids else "ambiguous",
            "pbs_partnerplayer",
            "PBS page has no unique partner-player ID",
            evidence={"player_ids": ids},
        )
    player_id = ids[0]
    player_url = f"https://player.pbs.org/widget/partnerplayer/{player_id}/"
    player = fetch_text(player_url)
    captions = list(
        dict.fromkeys(
            re.findall(r"https://[^\"'<> ]+?caption[^\"'<> ]+?\.(?:vtt|srt)(?:\?[^\"'<> ]*)?", player, re.I)
        )
    )
    redirects = list(
        dict.fromkeys(re.findall(r"https://urs\.pbs\.org/redirect/[^\"'<>\\ ]+", player, re.I))
    )
    cids = list(dict.fromkeys(re.findall(r"[0-9a-f]{8}-[0-9a-f-]{27,}", player, re.I)))
    if not redirects:
        return media_result(
            "no_media",
            "pbs_partnerplayer",
            "PBS player resolved but exposed no media encoding",
            provider="pbs",
            media_id=f"pbs:{player_id}",
            evidence={"player_url": player_url, "caption_count": len(captions)},
        )
    return media_result(
        "exact_timed_captions" if captions else "exact_episode_asr_required",
        "pbs_partnerplayer",
        "exact PBS player ID and official encoding resolved from transcript package",
        provider="pbs",
        media_id=f"pbs:{player_id}",
        media_url=redirects[-1],
        evidence={
            "player_url": player_url,
            "cid": cids[0] if cids else None,
            "caption_urls": captions,
            "encoding_count": len(redirects),
        },
    )


def discover_cbs(record: dict[str, Any]) -> dict[str, Any]:
    date = str(record.get("date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return media_result("no_media", "cbs_date_video", "package has no exact broadcast date")
    parsed = dt.date.fromisoformat(date)
    video_url = f"https://www.cbsnews.com/video/{parsed:%m%d}-face-the-nation/"
    page = fetch_text(video_url)
    title_probe = normalized_title(page[:250_000])
    expected = normalized_title(f"{parsed.month}/{parsed.day} face the nation")
    hls = list(
        dict.fromkeys(
            html.unescape(url)
            for url in re.findall(r"https://prod\.vodvideo\.cbsnews\.com/[^\"'<> ]+?master\.m3u8", page, re.I)
        )
    )
    if expected not in title_probe or len(hls) != 1:
        return media_result(
            "no_media" if not hls else "ambiguous",
            "cbs_date_video",
            "official date-derived page failed exact title/date/media checks",
            evidence={"video_page": video_url, "hls_count": len(hls), "expected_title": expected},
        )
    asset = re.search(r"/(\d+)_hls/master\.m3u8", hls[0])
    return media_result(
        "exact_episode_asr_required",
        "cbs_date_video",
        "date-derived official full-episode page passed title/date checks",
        provider="cbs-vod",
        media_id=f"cbs:{asset.group(1) if asset else sha256_text(hls[0])[:16]}",
        media_url=hls[0],
        evidence={"video_page": video_url, "expected_title": expected},
    )


def discover_fox_inline(record: dict[str, Any], page: str) -> dict[str, Any]:
    account = re.findall(r"(?:accountId|account_id|account)\D{0,10}(\d{9,})", page, re.I)
    video_ids = re.findall(r"(?:videoId|video_id|content_id)\D{0,10}(\d{9,})", page, re.I)
    mp4s = list(
        dict.fromkeys(
            re.sub(r"^http://", "https://", html.unescape(url))
            for url in re.findall(r"https?://vod\.foxnews\.com/[^\"'<> ]+?\.mp4(?:\?[^\"'<> ]*)?", page, re.I)
        )
    )
    if not video_ids or not mp4s:
        return media_result(
            "blocked_no_exact_media",
            "fox_inline_partial",
            "Fox transcript page exposes no usable official media asset",
        )
    account_id = account[0] if account else "694940094001"
    video_id = video_ids[0]
    durations = [int(value) for value in re.findall(r'"duration"\s*:\s*"?PT(?:(\d+)M)?', page) if value]
    return media_result(
        "partial_clip_asr_required",
        "fox_inline_partial",
        "official Fox page media is a candidate highlight; only aligned turns may advance",
        provider="fox-brightcove",
        media_id=f"fox:{account_id}:{video_id}",
        media_url=mp4s[0],
        evidence={
            "account_id": account_id,
            "video_id": video_id,
            "duration_minutes": durations[0] if durations else None,
            "package_url": record["package"],
            "package_id": str(record.get("package_id") or package_id(str(record["package"]))),
            "broadcast_date": record.get("date"),
            "show": record.get("show"),
        },
    )


def discover_youtube_page(record: dict[str, Any], page: str, adapter: str) -> dict[str, Any]:
    ids = youtube_ids(page)
    if len(ids) != 1:
        return media_result(
            "no_media" if not ids else "ambiguous",
            adapter,
            "official page exposes no unique YouTube asset",
            evidence={"youtube_ids": ids},
        )
    media_id = ids[0]
    return media_result(
        "exact_episode_asr_required",
        adapter,
        "official transcript/event page exposes one media ID; transcript ASR is still required",
        provider="youtube",
        media_id=f"youtube:{media_id}",
        media_url=f"https://www.youtube.com/watch?v={media_id}",
    )


def feed_by_date(feed: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for item in feed:
        if item.get("date") and item.get("media_url"):
            result[str(item["date"])].append(item)
    return result


def discover_fox_sunday(record: dict[str, Any], feed_map: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    date = str(record.get("date") or "")
    candidates = [
        item for item in feed_map.get(date, []) if "fox news sunday" in normalized_title(item.get("title", ""))
    ]
    if len(candidates) != 1:
        return media_result(
            "no_media" if not candidates else "ambiguous",
            "fox_news_sunday_rss",
            "official podcast feed has no unique exact-date full episode",
            evidence={"date": date, "candidate_count": len(candidates)},
        )
    item = candidates[0]
    media_id = item["guid"] or urllib.parse.urlsplit(item["media_url"]).path.rsplit("/", 1)[-1]
    return media_result(
        "exact_episode_asr_required",
        "fox_news_sunday_rss",
        "official full-episode podcast matched exact show and broadcast date",
        provider="fox-megaphone",
        media_id=f"fox-megaphone:{media_id}",
        media_url=item["media_url"],
        evidence={
            "feed_title": item["title"],
            "feed_date": item["date"],
            "guid": item["guid"],
            "package_url": record["package"],
            "package_id": str(record.get("package_id") or package_id(str(record["package"]))),
            "show": record.get("show"),
        },
    )


def discover_abc_this_week(
    record: dict[str, Any], feed_map: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Resolve one exact-date full episode from ABC Audio's official feed."""
    date = str(record.get("date") or "")
    matches = [
        item
        for item in feed_map.get(date, [])
        if "full episode" in normalized_title(item.get("title", ""))
        and str(item.get("media_url") or "").startswith("https://")
    ]
    if len(matches) != 1:
        return media_result(
            "no_media" if not matches else "ambiguous",
            "abc_this_week_official_rss",
            "ABC Audio feed has no unique exact-date full episode",
            evidence={"feed_date": date, "match_count": len(matches)},
        )
    item = matches[0]
    media_id = str(item.get("guid") or "").strip() or sha256_text(item["media_url"])[:20]
    return media_result(
        "exact_episode_asr_required",
        "abc_this_week_official_rss",
        "ABC Audio full episode matched the crawler transcript's exact broadcast date",
        provider="abc-megaphone",
        media_id=f"abc-megaphone:{media_id}",
        media_url=item["media_url"],
        evidence={
            "feed_title": item["title"],
            "feed_date": item["date"],
            "guid": item.get("guid"),
            "feed_url": "https://feeds.megaphone.fm/ESP8760518499?limit=1000",
        },
    )


def discover_abc_inline_segments(record: dict[str, Any], page: str) -> dict[str, Any]:
    """Return exact-date This Week segments embedded on the official page.

    Pages can contain unrelated ABC clips and mirrored CMAF/HLS URLs.  Admit
    only segment keys carrying the crawler row's broadcast date plus ``_tw_``,
    and retain one preferred official playlist per segment key.  Speaker and
    lexical binding remain downstream ASR/anchor decisions.
    """
    day = dt.date.fromisoformat(str(record.get("date") or ""))
    date_code = day.strftime("%y%m%d")
    urls = list(
        dict.fromkeys(
            html.unescape(value).replace("\\/", "/")
            for value in re.findall(r"https://[^\"'<> ]+?\.m3u8(?:\?[^\"'<> ]*)?", page, re.I)
        )
    )
    by_key: dict[str, str] = {}
    for url in urls:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.hostname or "").lower() not in {
            "cmp-abcnews.media.dssott.com",
            "service-pkgabcnews.akamaized.net",
        }:
            continue
        match = re.search(r"/abcnews/\d{4}/\d{2}/([^/?]+?)_,", parsed.path, re.I)
        if not match:
            continue
        key = match.group(1)
        if not key.lower().startswith(date_code.lower() + "_tw_"):
            continue
        current = by_key.get(key)
        if current is None or "cmp-abcnews.media.dssott.com" in url:
            by_key[key] = url
    candidates: list[dict[str, Any]] = []
    for key, url in sorted(by_key.items()):
        candidates.append(
            media_result(
                "candidate_asr_required",
                "abc_official_inline_segments",
                "official exact-date This Week segment; Whisper and exhaustive voice anchors required",
                provider="abc-official-video",
                media_id=f"abc-segment:{sha256_text(url)[:24]}",
                media_url=url,
                evidence={
                    "air_date": day.isoformat(),
                    "segment_key": key,
                    "package_url": str(record.get("package") or ""),
                },
            )
        )
    return media_result(
        "candidate_asr_required" if candidates else "no_media",
        "abc_official_inline_segments",
        (
            "official page exposes exact-date This Week segments requiring local ASR"
            if candidates
            else "official page exposes no exact-date This Week segment"
        ),
        evidence={"air_date": day.isoformat(), "segment_count": len(candidates)},
        candidates=candidates,
    )


def discover_axe(record: dict[str, Any], feed: Sequence[dict[str, Any]]) -> dict[str, Any]:
    match = re.search(r"/episodes/([a-f0-9-]{20,})", record["package"], re.I)
    guid = match.group(1).lower() if match else ""
    candidates = [item for item in feed if str(item.get("guid") or "").lower() == guid]
    if len(candidates) != 1 or not candidates[0].get("media_url"):
        return media_result(
            "no_media" if not candidates else "ambiguous",
            "axe_megaphone_guid",
            "official RSS has no unique enclosure for transcript GUID",
            evidence={"guid": guid, "candidate_count": len(candidates)},
        )
    item = candidates[0]
    return media_result(
        "exact_episode_asr_required",
        "axe_megaphone_guid",
        "CNN transcript package GUID exactly equals official RSS episode GUID",
        provider="megaphone",
        media_id=f"axe:{guid}",
        media_url=item["media_url"],
        evidence={"guid": guid, "feed_title": item["title"], "feed_date": item["date"]},
    )


def cnn_explicit_airtime(page: str) -> dt.datetime:
    """Parse the candidate page's own explicit CNN aired date/time."""
    plain = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.I | re.S)
    plain = html.unescape(re.sub(r"<[^>]+>", " ", plain))
    plain = re.sub(r"\s+", " ", plain)
    # Segment pages normally say "Aired August 28, 2026 - 06:30 ET".
    matches = re.findall(
        r"Aired\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})\s*-\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(?:-\s*\d{1,2}(?::\d{2})?)?\s*([ap])?\s*ET",
        plain,
        re.I,
    )
    if not matches:
        raise RecoveryError("CNN page exposes no explicit parseable aired date/time")
    month_raw, day_raw, year_raw, hour_raw, minute_raw, meridiem = matches[-1]
    hour = int(hour_raw)
    minute = int(minute_raw or 0)
    if meridiem:
        hour %= 12
        if meridiem.lower() == "p":
            hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise RecoveryError("CNN page has an invalid airtime")
    try:
        local_date = dt.datetime.strptime(
            f"{month_raw} {day_raw} {year_raw}", "%B %d %Y"
        ).date()
    except ValueError as exc:
        raise RecoveryError("CNN page has an invalid aired date") from exc
    local = dt.datetime.combine(local_date, dt.time(hour, minute), ZoneInfo("America/New_York"))
    return local.astimezone(dt.timezone.utc)


def cnn_airtime(record: dict[str, Any], page: str) -> dt.datetime:
    """Bind CNN's explicit page airtime to the crawler package date."""
    aired = cnn_explicit_airtime(page)
    try:
        expected_date = dt.date.fromisoformat(str(record.get("date") or ""))
    except ValueError as exc:
        raise RecoveryError("CNN package date is invalid") from exc
    if aired.astimezone(ZoneInfo("America/New_York")).date() != expected_date:
        raise RecoveryError("CNN page aired date differs from the crawler package date")
    return aired


def archive_search_prefix(prefix: str) -> list[dict[str, Any]]:
    query = urllib.parse.quote(f"identifier:{prefix}*", safe="")
    fields = "&fl%5B%5D=identifier&fl%5B%5D=title&fl%5B%5D=date"
    url = (
        "https://archive.org/advancedsearch.php?q="
        + query
        + fields
        + "&rows=20&page=1&output=json"
    )
    payload = fetch_json(url)
    return list(((payload or {}).get("response") or {}).get("docs") or [])


def archive_search_pattern(pattern: str) -> list[dict[str, Any]]:
    query = urllib.parse.quote(f"identifier:{pattern}", safe="")
    fields = "&fl%5B%5D=identifier&fl%5B%5D=title&fl%5B%5D=date"
    url = (
        "https://archive.org/advancedsearch.php?q="
        + query
        + fields
        + "&rows=50&page=1&output=json"
    )
    payload = fetch_json(url)
    return list(((payload or {}).get("response") or {}).get("docs") or [])


def archive_caption_minutes(page: str) -> tuple[list[str], list[tuple[float, float]]]:
    """Read public one-minute caption snippets embedded in a TV item page."""
    pattern = re.compile(
        r'<div class="tvcol"\s+data-idx="\d+".*?'
        r'href="/start/(\d+)/end/(\d+)".*?'
        r'<div class="snipin[^\"]*"[^>]*>(.*?)</div>',
        re.I | re.S,
    )
    tokens: list[str] = []
    times: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    for match in pattern.finditer(page):
        begin, end = int(match.group(1)), int(match.group(2))
        if (begin, end) in seen or end <= begin:
            continue
        seen.add((begin, end))
        text = html.unescape(re.sub(r"<[^>]+>", " ", match.group(3)))
        minute_tokens = normalize_tokens(text)
        width = (end - begin) / float(max(1, len(minute_tokens)))
        for index, token in enumerate(minute_tokens):
            tokens.append(token)
            times.append((begin + index * width, begin + (index + 1) * width))
    return tokens, times


def caption_spans_for_record(
    record: dict[str, Any], page: str, minimum_ratio: float = 0.64
) -> list[dict[str, Any]]:
    caption_tokens, caption_times = archive_caption_minutes(page)
    if len(caption_tokens) < 20:
        return []
    pointer = 0
    spans: list[dict[str, Any]] = []
    for turn in load_turns_for_record(record):
        match = ordered_alignment(
            normalize_tokens(turn["text"]), caption_tokens, pointer, minimum_ratio=minimum_ratio
        )
        if not match:
            continue
        begin, end, ratio = match
        pointer = end
        spans.append(
            {
                "rowid": turn["rowid"],
                "member": turn["member"],
                "start_sec": round(caption_times[begin][0], 3),
                "end_sec": round(caption_times[end - 1][1], 3),
                "caption_alignment_ratio": round(ratio, 6),
            }
        )
    return spans


def archive_media_metadata(identifier: str) -> dict[str, Any]:
    payload = fetch_json("https://archive.org/metadata/" + urllib.parse.quote(identifier, safe=""))
    files = list((payload or {}).get("files") or [])
    mp4s = [
        value
        for value in files
        if str(value.get("name") or "").lower().endswith(".mp4")
        and str(value.get("format") or "").lower() in {"h.264", "mpeg4", "mpeg4 video"}
    ]
    if len(mp4s) != 1:
        raise RecoveryError(f"archive item exposes {len(mp4s)} MP4 derivatives")
    return {"metadata": (payload or {}).get("metadata") or {}, "file": mp4s[0]}


def archive_identifier_airtime(identifier: str) -> str | None:
    timestamp = re.search(r"_(\d{8})_(\d{6})_", identifier)
    if not timestamp:
        return None
    return dt.datetime.strptime(
        timestamp.group(1) + timestamp.group(2), "%Y%m%d%H%M%S"
    ).replace(tzinfo=dt.timezone.utc).isoformat()


def discover_tvnews_program(
    record: dict[str, Any], channel: str, program_suffix: str, adapter: str,
    shared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    date_digits = str(record.get("date") or "").replace("-", "")
    if not re.fullmatch(r"\d{8}", date_digits):
        raise RecoveryError("TV transcript package date is invalid")
    pattern = f"{channel}_{date_digits}_*_{program_suffix}"
    docs = memoized_discovery(
        shared, "archive_search_pattern_cache", pattern,
        lambda: archive_search_pattern(pattern),
    )
    if len(docs) != 1:
        return media_result(
            "no_media" if not docs else "ambiguous",
            adapter,
            "program/date lookup has no unique TV archive item",
            evidence={"archive_pattern": pattern, "candidate_count": len(docs)},
        )
    identifier = str(docs[0]["identifier"])
    page_url = "https://archive.org/details/" + urllib.parse.quote(identifier, safe="")
    archive_page = memoized_discovery(
        shared, "archive_page_cache", identifier, lambda: fetch_text(page_url)
    )
    spans = caption_spans_for_record(record, archive_page)
    # An exact channel/program/date item is sufficient to acquire a recording
    # for local ASR, but never sufficient to identify a speaker.  Previously
    # this branch returned before resolving the MP4, leaving
    # ``candidate_asr_required`` records permanently unacquirable.
    media = memoized_discovery(
        shared, "archive_metadata_cache", identifier,
        lambda: archive_media_metadata(identifier),
    )
    file_row = media["file"]
    filename = str(file_row["name"])
    airtime = archive_identifier_airtime(identifier)
    media_url = (
        "https://archive.org/download/"
        + urllib.parse.quote(identifier, safe="")
        + "/"
        + urllib.parse.quote(filename, safe="._-")
    )
    common_evidence = {
        "identifier": identifier,
        "archive_page": page_url,
        "archive_pattern": pattern,
        "utc_airtime": airtime,
        "broadcast_title": docs[0].get("title"),
        "caption_spans": spans,
        "source_file": filename,
        "source_size": int(file_row.get("size") or 0),
        "source_md5": file_row.get("md5"),
        "source_sha1": file_row.get("sha1"),
        "source_private": str(file_row.get("private") or "").lower() == "true",
    }
    if not spans:
        return media_result(
            "candidate_asr_required",
            adapter,
            "exact channel/program/date item found; local Whisper transcript and speaker identity are required",
            provider="internet-archive-tvnews",
            media_id=f"ia:{identifier}",
            media_url=media_url,
            evidence={**common_evidence, "access": "public-full-program-asr"},
        )
    return media_result(
        "exact_timed_captions",
        adapter,
        "exact channel/program/date item and complete stored member turn aligned in timed captions",
        provider="internet-archive-tvnews",
        media_id=f"ia:{identifier}",
        media_url=media_url,
        evidence={**common_evidence, "access": "public-bounded-tv-clips"},
    )


def discover_cnn_archive(
    record: dict[str, Any], shared: dict[str, Any] | None = None
) -> dict[str, Any]:
    transcript_page = fetch_text(record["package"])
    aired_utc = cnn_airtime(record, transcript_page)
    hour = aired_utc.replace(minute=0, second=0, microsecond=0)
    prefix = "CNNW_" + hour.strftime("%Y%m%d_%H0000_")
    docs = memoized_discovery(
        shared, "archive_search_prefix_cache", prefix,
        lambda: archive_search_prefix(prefix),
    )
    if len(docs) != 1:
        return media_result(
            "no_media" if not docs else "ambiguous",
            "cnn_tvnews_airtime",
            "archive lookup has no unique CNN broadcast hour",
            evidence={"utc_hour": hour.isoformat(), "archive_prefix": prefix, "candidate_count": len(docs)},
        )
    identifier = str(docs[0]["identifier"])
    archive_page_url = "https://archive.org/details/" + urllib.parse.quote(identifier, safe="")
    archive_page = memoized_discovery(
        shared, "archive_page_cache", identifier, lambda: fetch_text(archive_page_url)
    )
    spans = caption_spans_for_record(record, archive_page)
    media = memoized_discovery(
        shared, "archive_metadata_cache", identifier,
        lambda: archive_media_metadata(identifier),
    )
    file_row = media["file"]
    filename = str(file_row["name"])
    media_url = (
        "https://archive.org/download/"
        + urllib.parse.quote(identifier, safe="")
        + "/"
        + urllib.parse.quote(filename, safe="._-")
    )
    common_evidence = {
        "identifier": identifier,
        "archive_page": archive_page_url,
        "utc_airtime": aired_utc.isoformat(),
        "archive_identifier_airtime_utc": archive_identifier_airtime(identifier),
        "expected_program_label": cnn_program_label(record),
        "broadcast_title": docs[0].get("title"),
        "caption_spans": spans,
        "source_file": filename,
        "source_size": int(file_row.get("size") or 0),
        "source_md5": file_row.get("md5"),
        "source_sha1": file_row.get("sha1"),
        "source_private": str(file_row.get("private") or "").lower() == "true",
    }
    if not spans:
        return media_result(
            "candidate_asr_required",
            "cnn_tvnews_airtime",
            "exact CNN channel/hour found; local Whisper transcript and speaker identity are required",
            provider="internet-archive-tvnews",
            media_id=f"ia:{identifier}",
            media_url=media_url,
            evidence={**common_evidence, "access": "public-full-program-asr"},
        )
    return media_result(
        "exact_timed_captions",
        "cnn_tvnews_airtime+public_caption_alignment",
        "CNN channel/hour and stored member turn matched the archive's public timed-caption snippets",
        provider="internet-archive-tvnews",
        media_id=f"ia:{identifier}",
        media_url=media_url,
        evidence={**common_evidence, "access": "public-bounded-tv-clips"},
    )


_PHRASE_STOP = {
    "about", "after", "again", "because", "before", "could", "every", "first",
    "from", "going", "have", "into", "just", "more", "other", "people", "really",
    "should", "some", "than", "that", "their", "there", "these", "they", "think",
    "this", "those", "through", "very", "want", "what", "when", "where", "which",
    "with", "would", "your",
}


def distinctive_five_word_phrase(text: str) -> str:
    tokens = normalize_tokens(text)
    if len(tokens) < 5:
        raise RecoveryError("transcript turn is too short for TV-caption search")
    candidates: list[tuple[float, int, list[str]]] = []
    for index in range(len(tokens) - 4):
        window = tokens[index : index + 5]
        content = sum(1 for token in window if token not in _PHRASE_STOP and len(token) >= 4)
        length = sum(min(len(token), 10) for token in window)
        diversity = len(set(window))
        score = content * 20 + length + diversity * 2
        candidates.append((score, -index, window))
    return " ".join(max(candidates)[2])


def gdelt_tv_clips(record: dict[str, Any], station: str) -> tuple[str, list[dict[str, Any]]]:
    turns = load_turns_for_record(record)
    if not turns:
        return "", []
    turn = max(turns, key=lambda value: (len(normalize_tokens(value["text"])), -value["rowid"]))
    phrase = distinctive_five_word_phrase(turn["text"])
    date_value = dt.date.fromisoformat(str(record["date"]))
    begin = dt.datetime.combine(date_value, dt.time.min, dt.timezone.utc)
    # Eastern late-night programs can fall on the next UTC date.
    end = begin + dt.timedelta(days=2)
    query = f'"{phrase}" station:{station}'
    url = (
        "https://api.gdeltproject.org/api/v2/tv/tv?query="
        + urllib.parse.quote(query, safe="")
        + "&mode=clipgallery&format=json&maxrecords=50&startdatetime="
        + begin.strftime("%Y%m%d%H%M%S")
        + "&enddatetime="
        + end.strftime("%Y%m%d%H%M%S")
    )
    payload = fetch_json(url, timeout=120)
    if not isinstance(payload, dict):
        raise RecoveryError("GDELT TV API returned a non-object response")
    return phrase, list(payload.get("clips") or [])


def archive_tv_clips(record: dict[str, Any], channel: str) -> tuple[str, list[dict[str, Any]]]:
    """Query Archive.org's current public TV full-text backend.

    The former ``/details/tv?...&output=json`` endpoint now serves only the
    interactive web shell.  This is the public JSON service used by that shell.
    We still treat its hits as candidates and require full stored-turn caption
    alignment on the corresponding item page before binding any recording.
    """
    turns = load_turns_for_record(record)
    if not turns:
        return "", []
    turn = max(turns, key=lambda value: (len(normalize_tokens(value["text"])), -value["rowid"]))
    phrase = distinctive_five_word_phrase(turn["text"])
    params = urllib.parse.urlencode({
        "service_backend": "tvs",
        "user_query": phrase,
        "hits_per_page": "100",
        "page": "1",
    })
    payload = fetch_json(
        "https://archive.org/services/search/beta/page_production/?" + params,
        timeout=60,
    )
    hits = (((payload or {}).get("response") or {}).get("body") or {}).get("hits") or {}
    if not isinstance(hits, dict):
        raise RecoveryError("Archive.org TV search returned no hit envelope")
    try:
        local_day = dt.date.fromisoformat(str(record["date"]))
    except ValueError as exc:
        raise RecoveryError("TV transcript package date is invalid") from exc
    allowed_days = {local_day.strftime("%Y%m%d"), (local_day + dt.timedelta(days=1)).strftime("%Y%m%d")}
    clips: list[dict[str, Any]] = []
    for hit in hits.get("hits") or []:
        if not isinstance(hit, dict) or hit.get("hit_type") != "tv_clip":
            continue
        fields = hit.get("fields") or {}
        identifier = str(fields.get("identifier") or "")
        match = re.match(rf"^{re.escape(channel)}_(\d{{8}})_", identifier)
        if not match or match.group(1) not in allowed_days:
            continue
        highlight = hit.get("highlight") or {}
        clips.append({
            "ia_show_id": identifier,
            "show": fields.get("title"),
            "show_date": fields.get("date") or fields.get("addeddate"),
            "preview_url": "https://archive.org" + str(fields.get("__href__") or ""),
            "snippet": " ".join(str(value) for value in (highlight.get("text") or [])),
            "start": fields.get("start"),
            "search_backend": "archive-org-tvs-page-production",
        })
    return phrase, clips


def discover_tvnews_phrase(
    record: dict[str, Any], station: str, identifier_prefix: str, adapter: str,
    shared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        date_value = dt.date.fromisoformat(str(record.get("date") or ""))
    except ValueError as exc:
        raise RecoveryError("TV transcript package date is invalid") from exc
    if date_value < dt.date(2009, 7, 2):
        return media_result(
            "blocked_no_public_media_id",
            adapter,
            "package predates the searchable TV-caption archive",
            evidence={"archive_coverage_starts": "2009-07-02"},
        )
    channel = identifier_prefix.rstrip("_")
    try:
        phrase, clips = archive_tv_clips(record, channel)
    except (RecoveryError, urllib.error.URLError, urllib.error.HTTPError):
        # Retain GDELT as a compatibility fallback; Archive.org's own current
        # TV search service is the primary resolver and avoids that endpoint's
        # recurrent connection resets.
        phrase, clips = gdelt_tv_clips(record, station)
    identifiers: dict[str, dict[str, Any]] = {}
    for clip in clips:
        identifier = str(clip.get("ia_show_id") or "")
        if not identifier.startswith(identifier_prefix):
            continue
        identifiers.setdefault(identifier, clip)
    resolved: list[dict[str, Any]] = []
    for identifier, clip in identifiers.items():
        archive_page_url = "https://archive.org/details/" + urllib.parse.quote(identifier, safe="")
        archive_page = memoized_discovery(
            shared, "archive_page_cache", identifier,
            lambda url=archive_page_url: fetch_text(url),
        )
        spans = caption_spans_for_record(record, archive_page)
        media = memoized_discovery(
            shared, "archive_metadata_cache", identifier,
            lambda value=identifier: archive_media_metadata(value),
        )
        file_row = media["file"]
        filename = str(file_row["name"])
        resolved.append(
            {
                "identifier": identifier,
                "clip": clip,
                "archive_page": archive_page_url,
                "caption_spans": spans,
                "file": file_row,
                "media_url": (
                    "https://archive.org/download/"
                    + urllib.parse.quote(identifier, safe="")
                    + "/"
                    + urllib.parse.quote(filename, safe="._-")
                ),
            }
        )
    if len(resolved) != 1:
        return media_result(
            "no_media" if not resolved else "ambiguous",
            adapter,
            "no unique channel/date TV broadcast candidate was resolved",
            evidence={
                "query_phrase": phrase,
                "station": station,
                "caption_hits": len(clips),
                "archive_items": sorted(identifiers),
                "resolved_items": [value["identifier"] for value in resolved],
            },
        )
    item = resolved[0]
    file_row = item["file"]
    clip_start = item["clip"].get("start")
    try:
        asr_hint_start_sec = float(clip_start) if clip_start is not None else None
    except (TypeError, ValueError):
        asr_hint_start_sec = None
    common_evidence = {
        "identifier": item["identifier"],
        "archive_page": item["archive_page"],
        "query_phrase": phrase,
        "caption_preview_url": item["clip"].get("preview_url"),
        "caption_snippet": item["clip"].get("snippet"),
        "caption_search_backend": item["clip"].get("search_backend") or "gdelt-tv-v2",
        "broadcast_title": item["clip"].get("show"),
        "utc_airtime": archive_identifier_airtime(item["identifier"]),
        "caption_spans": item["caption_spans"],
        "asr_hint_start_sec": asr_hint_start_sec,
        "source_file": file_row.get("name"),
        "source_size": int(file_row.get("size") or 0),
        "source_md5": file_row.get("md5"),
        "source_sha1": file_row.get("sha1"),
        "source_private": str(file_row.get("private") or "").lower() == "true",
    }
    if not item["caption_spans"]:
        return media_result(
            "candidate_asr_required",
            adapter + "+local_whisper_required",
            "one channel/date broadcast contained the distinctive phrase; local Whisper must bind the complete turn",
            provider="internet-archive-tvnews",
            media_id=f"ia:{item['identifier']}",
            media_url=item["media_url"],
            evidence={**common_evidence, "access": "public-search-hit-bounded-asr"},
        )
    return media_result(
        "exact_timed_captions",
        adapter,
        "distinctive stored phrase found one broadcast and the complete member turn aligned in its timed captions",
        provider="internet-archive-tvnews",
        media_id=f"ia:{item['identifier']}",
        media_url=item["media_url"],
        evidence={**common_evidence, "access": "public-bounded-tv-clips"},
    )
def cnn_search_query(record: dict[str, Any], member: str, text: str) -> str:
    path = urllib.parse.urlsplit(record["package"]).path
    show_match = re.search(r"/show/([^/]+)", path)
    show = show_match.group(1).replace("-", " ") if show_match else str(record.get("show") or "CNN")
    tokens = [token for token in normalize_tokens(text) if len(token) >= 5]
    phrase = " ".join(tokens[:7])
    return " ".join(part for part in ["CNN", show, str(record.get("date") or ""), member, phrase] if part)


def cnn_program_label(record: dict[str, Any]) -> str:
    path = urllib.parse.urlsplit(str(record.get("package") or "")).path
    match = re.search(r"/show/([^/]+)", path)
    code = (match.group(1) if match else str(record.get("show") or "")).lower()
    return CNN_SHOW_LABELS.get(code, normalized_title(str(record.get("show") or code)))


def official_cnn_search_candidates(record: dict[str, Any], db_path: Path = DB_PATH, limit: int = 12) -> list[dict[str, Any]]:
    """Search YouTube, retaining only the official CNN channel.

    This stage discovers candidates; it never claims a transcript binding.
    The subsequent ``align`` stage must find the stored text in the audio.
    """
    rowids = [rowid for member in record["members"] for rowid in member["rowids"]]
    if not rowids:
        return []
    uri = "file:" + db_path.resolve().as_posix() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        first = con.execute(
            "SELECT member,text FROM speeches WHERE rowid=? AND source='INTERVIEW'", (rowids[0],)
        ).fetchone()
    finally:
        con.close()
    if not first:
        return []
    query = cnn_search_query(record, str(first[0]), str(first[1]))
    transcript_airtime_utc = None
    try:
        transcript_airtime_utc = cnn_airtime(
            record, fetch_text(record["package"])
        ).isoformat()
    except (
        RecoveryError,
        OSError,
        urllib.error.URLError,
        urllib.error.HTTPError,
    ):
        # Optional descriptive evidence only.  The candidate must still pass
        # local transcript alignment and exhaustive voice identity.
        transcript_airtime_utc = None
    expected_program_label = cnn_program_label(record)
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end",
        str(limit),
        "ytsearch%d:%s" % (limit, query),
    ]
    try:
        process = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=YT_DLP_SEARCH_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise RecoveryError(
            "yt-dlp CNN search timed out after %ds" % YT_DLP_SEARCH_TIMEOUT_SEC
        ) from exc
    if process.returncode:
        raise RecoveryError("yt-dlp CNN search failed: " + process.stderr[-500:])
    payload = json.loads(process.stdout)
    output: list[dict[str, Any]] = []
    for item in payload.get("entries") or []:
        channel_id = str(item.get("channel_id") or item.get("uploader_id") or "")
        if channel_id not in OFFICIAL_YOUTUBE_CHANNELS["cnn"]:
            continue
        video_id = str(item.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            continue
        candidate_airtime_utc = None
        try:
            description = str(item.get("description") or "")
            if "aired" in description.lower():
                candidate_airtime_utc = cnn_explicit_airtime(description).isoformat()
        except RecoveryError:
            candidate_airtime_utc = None
        output.append(
            {
                "provider": "youtube",
                "media_id": f"youtube:{video_id}",
                "media_url": f"https://www.youtube.com/watch?v={video_id}",
                "title": item.get("title"),
                "channel_id": channel_id,
                "duration": item.get("duration"),
                "query": query,
                "status": "candidate_asr_required",
                "evidence": {
                    "package_url": record["package"],
                    "package_id": str(record.get("package_id") or package_id(str(record["package"]))),
                    "broadcast_date": record.get("date"),
                    "show": record.get("show"),
                    "transcript_airtime_utc": transcript_airtime_utc,
                    "candidate_broadcast_airtime_utc": candidate_airtime_utc,
                    "official_channel_id": channel_id,
                    "search_result_title": item.get("title"),
                    "expected_program_label": expected_program_label,
                    "video_timestamp": item.get("release_timestamp") or item.get("timestamp"),
                    "upload_date": item.get("upload_date"),
                    "video_id": video_id,
                },
            }
        )
    return output


def discover_one(
    record: dict[str, Any],
    shared: dict[str, Any],
    *,
    cnn_search: bool = False,
) -> dict[str, Any]:
    family = record["source_family"]
    if record.get("discovery", {}).get("status") == "prepared":
        return record["discovery"]
    if family == "cnn":
        try:
            archive = discover_cnn_archive(record, shared)
        except (
            RecoveryError,
            OSError,
            urllib.error.URLError,
            urllib.error.HTTPError,
            ET.ParseError,
        ) as exc:
            # Airtime/program parsing is one archive-discovery accelerator,
            # never an identity requirement.  When it is missing or stale,
            # continue to candidate-specific official media search so local
            # transcript alignment and exhaustive voice identity can decide.
            archive = media_result(
                "probe_error",
                "cnn_tvnews_airtime",
                "archive accelerator failed; candidate-specific media search remains eligible",
                evidence={"archive_error": str(exc)[:500]},
            )
        archive_private_value = (archive.get("evidence") or {}).get("source_private")
        archive_private = (
            archive_private_value is True
            or str(archive_private_value).lower() == "true"
        )
        if (
            archive["status"] in {"exact_timed_captions", "candidate_asr_required"}
            and not archive_private
        ):
            return archive
        if cnn_search:
            candidates = official_cnn_search_candidates(record)
            if candidates:
                archive_evidence = dict(archive.get("evidence") or {})
                return media_result(
                    "candidate_asr_required",
                    "cnn_official_candidate_search",
                    "candidate-specific official CNN media require local transcript alignment and voice identity",
                    evidence={
                        "archive_accelerator_status": archive.get("status"),
                        "archive_accelerator_private": archive_private,
                        "archive_accelerator_evidence": archive_evidence,
                        "program_airtime_used_for_identity": False,
                    },
                    candidates=candidates,
                )
        return archive
    if family == "cbs-ftn":
        return discover_cbs(record)
    if family == "fox":
        date_value = dt.date.fromisoformat(str(record.get("date") or ""))
        # The crawler package is the primary Fox source.  Probe its official
        # inline player before the much slower TV-news archive phrase search;
        # an official highlight still remains candidate-only until local ASR
        # binds the stored transcript turns.
        inline_error = ""
        try:
            page = fetch_text(record["package"])
            inline = discover_fox_inline(record, page)
            if inline["status"] == "partial_clip_asr_required":
                return inline
        except (RecoveryError, OSError, urllib.error.URLError) as exc:
            inline_error = str(exc)[:500]
        if "sunday" in normalized_title(str(record.get("show") or "")):
            try:
                program = discover_tvnews_program(
                    record,
                    "FOXNEWSW",
                    "FOX_News_Sunday",
                    "fox_archive_program_date+public_caption_alignment",
                    shared,
                )
                if program["status"] in {"exact_timed_captions", "candidate_asr_required"}:
                    return program
            except RecoveryError:
                pass
        if dt.date(2009, 7, 2) <= date_value <= dt.date(2024, 10, 5):
            try:
                archive = discover_tvnews_phrase(
                    record, "FOXNEWS", "FOXNEWSW_", "fox_gdelt_tvnews_caption_alignment",
                    shared,
                )
                if archive["status"] in {"exact_timed_captions", "candidate_asr_required"}:
                    return archive
            except RecoveryError:
                # The GDELT TV endpoint is intermittently reset-prone.  Exact
                # official episode feeds remain a valid independent resolver;
                # do not discard them merely because the optional archive
                # accelerator is temporarily unavailable.
                pass
        if "sunday" in normalized_title(str(record.get("show") or "")):
            feed = discover_fox_sunday(record, shared["fox_by_date"])
            if feed["status"] == "exact_episode_asr_required":
                return feed
        if inline_error:
            return media_result(
                "probe_error",
                "fox_inline_partial",
                "official Fox crawler page could not be read and no exact fallback resolved it",
                evidence={"page_error": inline_error},
            )
        return inline
    if family == "cnn-axe-files":
        return discover_axe(record, shared["axe_feed"])
    if family == "abc-this-week":
        program = discover_tvnews_program(
            record,
            "KGO",
            "This_Week_With_George_Stephanopoulos",
            "abc_kgo_program_date+local_whisper_alignment",
            shared,
        )
        if program["status"] == "exact_timed_captions":
            return program
        official = discover_abc_this_week(record, shared.get("abc_by_date", {}))
        if official["status"] == "exact_episode_asr_required":
            return official
        try:
            inline = discover_abc_inline_segments(record, fetch_text(record["package"]))
            if inline["status"] == "candidate_asr_required":
                return inline
        except (RecoveryError, OSError, urllib.error.URLError):
            pass
        return program
    if family == "msnow":
        return discover_tvnews_phrase(
            record, "MSNBC", "MSNBCW_", "msnbc_gdelt_tvnews_caption_alignment",
            shared,
        )
    if family == "nbc-mtp":
        return media_result(
            "blocked_no_exact_media",
            "nbc_simplecast",
            "official Simplecast episode resolver is not yet proven for this exact date/package",
        )
    page = fetch_text(record["package"])
    if family == "npr":
        return discover_npr(record, page)
    if family == "pbs":
        return discover_pbs(record, page)
    if family == "cfr":
        return discover_youtube_page(record, page, "cfr_videoobject")
    if family == "csis":
        event_links = list(
            dict.fromkeys(
                urllib.parse.urljoin(record["package"], value)
                for value in re.findall(r'href=["\']([^"\']+/events/[^"\']+)["\']', page, re.I)
            )
        )
        candidates = youtube_ids(page)
        for event in event_links[:4]:
            candidates.extend(youtube_ids(fetch_text(event)))
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) == 1:
            value = dict(record)
            joined = f'<iframe src="https://www.youtube.com/embed/{candidates[0]}"></iframe>'
            return discover_youtube_page(value, joined, "csis_event_oembed")
        return media_result(
            "no_media" if not candidates else "ambiguous",
            "csis_event_oembed",
            "analysis/event chain exposes no unique YouTube asset",
            evidence={"event_links": event_links, "youtube_ids": candidates},
        )
    if family == "aspen":
        return media_result(
            "blocked_no_exact_media",
            "aspen_attachment_session",
            "PDF package requires attachment-to-session mapping before media can be admitted",
        )
    if family == "hugh-hewitt":
        return media_result(
            "blocked_no_exact_media",
            "hugh_hewitt_omny",
            "date/member candidate must be resolved against the official Omny feed and then ASR-verified",
        )
    if family == "cspan":
        return media_result(
            "blocked_no_exact_media",
            "cspan_existing_lane",
            "package is outside the currently accepted timed Washington Journal subset",
        )
    return media_result("no_media", family, "no official media adapter is defined")


def shared_discovery_state(families: set[str]) -> dict[str, Any]:
    # Exact Archive.org search keys and identifiers recur across transcript
    # packages for the same broadcast. Cache only immutable discovery
    # responses; per-package caption alignment still runs independently.
    state: dict[str, Any] = {
        "archive_search_prefix_cache": ConcurrentMemo(128),
        "archive_search_pattern_cache": ConcurrentMemo(128),
        "archive_page_cache": ConcurrentMemo(64),
        "archive_metadata_cache": ConcurrentMemo(128),
    }
    if "fox" in families:
        fox_feed = parse_feed("https://feeds.megaphone.fm/FOXM8305830891?limit=1000")
        state["fox_by_date"] = feed_by_date(fox_feed)
    else:
        state["fox_by_date"] = {}
    if "abc-this-week" in families:
        abc_feed = parse_feed("https://feeds.megaphone.fm/ESP8760518499?limit=1000")
        state["abc_by_date"] = feed_by_date(abc_feed)
    else:
        state["abc_by_date"] = {}
    if "cnn-axe-files" in families:
        state["axe_feed"] = parse_feed("https://feeds.megaphone.fm/WMHY9284588779")
    else:
        state["axe_feed"] = []
    return state


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    status = collections.Counter()
    turns = collections.Counter()
    words = collections.Counter()
    for row in rows:
        family = row["source_family"]
        value = str((row.get("discovery") or {}).get("status") or "missing")
        by_source[family][value] += 1
        status[value] += 1
        turns[family] += int(row.get("turn_count") or 0)
        words[family] += int(row.get("word_count") or 0)
    # MS NOW rows were initially marked blocked before the current Archive.org
    # TV full-text resolver was proven.  Those legacy dispositions must be
    # revisited; do not misreport them as complete merely because their old
    # status string was terminal under the earlier capability set.
    stale_resolver_blocks = by_source["msnow"]["blocked_no_exact_media"]
    pending = sum(status[value] for value in RETRYABLE_DISCOVERY) + stale_resolver_blocks
    return {
        "schema": SCHEMA,
        "generated_at": utc_now(),
        "packages": len(rows),
        "turns": sum(int(row.get("turn_count") or 0) for row in rows),
        "words": sum(int(row.get("word_count") or 0) for row in rows),
        "discovery_status": dict(sorted(status.items())),
        "all_packages_dispositioned": pending == 0,
        "pending_packages": pending,
        "stale_dispositions_requiring_reprobe": stale_resolver_blocks,
        "by_source": {
            family: {
                "packages": sum(counter.values()),
                "turns": turns[family],
                "words": words[family],
                "status": dict(sorted(counter.items())),
            }
            for family, counter in sorted(by_source.items())
        },
    }


def normalize_private_discovery(row: dict[str, Any]) -> bool:
    discovery = row.get("discovery") or {}
    private_value = (discovery.get("evidence") or {}).get("source_private")
    source_private = private_value is True or str(private_value).lower() == "true"
    if not source_private or discovery.get("status") not in ACQUIRABLE:
        return False
    normalized = dict(discovery)
    evidence = dict(discovery.get("evidence") or {})
    evidence["private_source_original_status"] = discovery.get("status")
    normalized.update({
        "status": "blocked_no_public_media_id",
        "reason": (
            "the resolved archive recording is private; it remains unresolved "
            "until a public recording can pass transcript and voice verification"
        ),
        "evidence": evidence,
    })
    row["discovery"] = normalized
    return True


def inventory_sidecars(path: Path) -> tuple[Path, Path]:
    """Keep source-isolated snapshots from mutating the live inventory chain."""
    try:
        canonical = path.resolve() == INVENTORY_PATH.resolve()
    except OSError:
        canonical = False
    if canonical:
        return SUMMARY_PATH, RECEIPT_PATH
    base = path.with_suffix("") if path.suffix.lower() == ".jsonl" else path
    return (
        base.with_name(base.name + ".summary.json"),
        base.with_name(base.name + ".receipt.json"),
    )


def persist(rows: Sequence[dict[str, Any]], path: Path = INVENTORY_PATH) -> dict[str, Any]:
    for row in rows:
        normalize_private_discovery(row)
    ordered = sorted(rows, key=lambda row: row["package"])
    write_jsonl_atomic(path, ordered)
    summary = summarize(ordered)
    summary_path, receipt_path = inventory_sidecars(path)
    write_json_atomic(summary_path, summary)
    receipt = {
        "schema": "speech-source-media-inventory-receipt-v1",
        "generated_at": utc_now(),
        "inventory_path": str(path.resolve()),
        "inventory_sha256": sha256_file(path),
        "summary_path": str(summary_path.resolve()),
        "summary_sha256": sha256_file(summary_path),
        "packages": len(ordered),
        "status": "COMPLETE" if summary["all_packages_dispositioned"] else "IN_PROGRESS",
    }
    write_json_atomic(receipt_path, receipt)
    return summary


def run_inventory(args: argparse.Namespace) -> int:
    rows = snapshot_inventory(Path(args.db))
    summary = persist(rows, Path(args.inventory))
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


def bounded_executor_results(
    executor: concurrent.futures.Executor,
    items: Iterable[Any],
    operation: Callable[[Any], Any],
    *,
    max_in_flight: int,
) -> Iterator[tuple[Any, Any]]:
    """Yield completed work without materializing one Future per package."""
    limit = max(1, int(max_in_flight))
    iterator = iter(items)
    pending: dict[concurrent.futures.Future[Any], Any] = {}

    def submit_one() -> bool:
        try:
            item = next(iterator)
        except StopIteration:
            return False
        pending[executor.submit(operation, item)] = item
        return True

    for _ in range(limit):
        if not submit_one():
            break
    while pending:
        completed, _ = concurrent.futures.wait(
            pending, return_when=concurrent.futures.FIRST_COMPLETED
        )
        for future in completed:
            item = pending.pop(future)
            yield item, future.result()
            submit_one()


def run_discover(args: argparse.Namespace) -> int:
    path = Path(args.inventory)
    rows = read_jsonl(path, required=True)
    selected = {value.strip() for value in (args.source or "").split(",") if value.strip()}
    families = {row["source_family"] for row in rows if not selected or row["source_family"] in selected}
    shared = shared_discovery_state(families)
    member_targets = load_member_gap(getattr(args, "member_gap", ""))
    eligible = []
    for row in rows:
        if selected and row["source_family"] not in selected:
            continue
        if args.package_id and row["package_id"] != args.package_id:
            continue
        current = str((row.get("discovery") or {}).get("status") or "needs_probe")
        retry_blocked = args.retry_blocked and current in {
            "blocked_no_exact_media", "blocked_no_public_media_id", "probe_error"
        }
        if current in TERMINAL_DISCOVERY and not args.refresh and not retry_blocked:
            continue
        if member_gap_priority(row, member_targets) is None:
            continue
        eligible.append(row)
    eligible.sort(key=lambda row: member_gap_priority(row, member_targets))
    if args.max_packages:
        eligible = eligible[: args.max_packages]

    def probe(row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        try:
            result = discover_one(row, shared, cnn_search=args.cnn_search)
            # Some adapters deliberately convert provider failures (for
            # example HTTP 429 or an archive timeout) into a structured
            # probe_error disposition.  Count those as errors even though no
            # exception crosses this boundary; otherwise the checkpoint can
            # misleadingly report errors=0 while every attempted row failed.
            failed = str(result.get("status") or "") == "probe_error"
        except (RecoveryError, urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, OSError) as exc:
            result = media_result(
                "probe_error", str((row.get("discovery") or {}).get("adapter") or row["source_family"]), str(exc)[:500]
            )
            failed = True
        if args.pause:
            time.sleep(args.pause)
        return result, failed

    processed = 0
    errors = 0
    workers = max(1, int(args.workers))
    executor = (
        concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        if workers > 1 else None
    )

    def completed_probes() -> Iterator[tuple[dict[str, Any], tuple[dict[str, Any], bool]]]:
        if executor is None:
            for row in eligible:
                yield row, probe(row)
            return
        yield from bounded_executor_results(
            executor, eligible, probe, max_in_flight=max(workers, workers * 4)
        )

    try:
        for row, (discovery, failed) in completed_probes():
            row["discovery"] = discovery
            processed += 1
            errors += int(failed)
            if processed % max(1, args.checkpoint_every) == 0:
                persist(rows, path)
                print(f"CHECKPOINT processed={processed} errors={errors}", flush=True)
    finally:
        if executor is not None:
            executor.shutdown(wait=True)
    summary = persist(rows, path)
    print(json.dumps({"processed": processed, "errors": errors, "summary": summary}, sort_keys=True))
    return 2 if errors else 0


def safe_media_key(media_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", media_id).strip("._")
    if not slug:
        raise RecoveryError("media ID has no safe filesystem representation")
    return slug[:180]


def direct_audio_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    path = urllib.parse.urlsplit(url).path.lower()
    return (
        path.endswith((".mp3", ".m4a", ".mp4", ".wav", ".aac", ".m3u8"))
        or host.endswith("megaphone.fm")
        or host.endswith("omnycontent.com")
        or host.endswith("npr.org")
        or host.endswith("pbs.org")
        or host.endswith("foxnews.com")
    )


def ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        value = shutil.which("ffmpeg")
        if not value:
            raise RecoveryError("ffmpeg is unavailable")
        return value


def candidate_discoveries(row: dict[str, Any]) -> list[dict[str, Any]]:
    discovery = row.get("discovery") or {}
    values = []
    source_private = bool((discovery.get("evidence") or {}).get("source_private"))
    if (
        discovery.get("media_id")
        and discovery.get("media_url")
        and not source_private
    ):
        values.append(discovery)
    values += list(discovery.get("candidates") or []) + list(
        discovery.get("fallback_candidates") or []
    )
    return [
        value
        for value in values
        if (
            isinstance(value, dict)
            and value.get("media_id")
            and value.get("media_url")
            and not (
                (value.get("evidence") or {}).get("source_private") is True
                or str((value.get("evidence") or {}).get("source_private")).lower()
                == "true"
            )
        )
    ]


def bound_candidate_provenance(
    record: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """Bind media to current discovery or a secured inventory acquisition.

    Discovery can improve after audio was acquired. A superseded discovery
    must not orphan a source whose current inventory still contains the exact
    provider/media/WAV/hash/reference acquisition receipt. Program and airtime
    never participate in this provenance decision.
    """
    media_id = str(candidate.get("media_id") or "")
    provider = str(candidate.get("provider") or "")
    for discovery in candidate_discoveries(record):
        if (
            str(discovery.get("media_id") or "") == media_id
            and str(discovery.get("provider") or "") == provider
            and discovery.get("status") in ACQUIRABLE
        ):
            evidence = discovery.get("evidence") or {}
            if not isinstance(evidence, dict) or bool(evidence.get("source_private")):
                continue
            binding = {
                "kind": "current_discovery",
                "provider": provider,
                "media_id": media_id,
                "reference_url": str(discovery.get("media_url") or ""),
                "evidence": evidence,
            }
            return {**binding, "binding_sha256": sha256_json(binding)}

    acquisition = record.get("acquisition") or {}
    items = list(acquisition.get("items") or [])
    if not items and acquisition.get("wav"):
        items = [acquisition]
    candidate_wav = Path(str(candidate.get("source_wav") or ""))
    candidate_source_sha = str(candidate.get("source_audio_sha256") or "")
    for item in items:
        item_wav = Path(str(item.get("wav") or ""))
        archive_source = item.get("archive_source") or {}
        reference_url = str(
            item.get("reference_url")
            or item.get("media_url")
            or candidate.get("reference_url")
            or ""
        )
        if (
            str(item.get("media_id") or "") != media_id
            or str(item.get("provider") or "") != provider
            or not candidate_wav.is_absolute()
            or not item_wav.is_absolute()
            or candidate_wav.resolve() != item_wav.resolve()
            or str(item.get("wav_sha256") or "") != candidate_source_sha
            or not reference_url.startswith(("https://", "http://"))
            or bool(item.get("source_private"))
            or bool(archive_source.get("source_private"))
            or "private" in str(archive_source.get("access") or "").lower()
        ):
            continue
        binding = {
            "kind": "current_secured_acquisition",
            "provider": provider,
            "media_id": media_id,
            "source_wav": str(item_wav.resolve()),
            "source_audio_sha256": candidate_source_sha,
            "reference_url": reference_url,
            "acquisition_sha256": sha256_json(item),
        }
        return {**binding, "binding_sha256": sha256_json(binding)}
    raise RecoveryError(
        "candidate media lacks current discovery or secured acquisition provenance"
    )


def caption_span_groups(discovery: dict[str, Any], maximum_window_sec: float = 90.0) -> list[list[dict[str, Any]]]:
    spans = sorted(
        (value for value in ((discovery.get("evidence") or {}).get("caption_spans") or [])
         if isinstance(value, dict)),
        key=lambda value: (float(value.get("start_sec") or 0), float(value.get("end_sec") or 0)),
    )
    groups: list[list[dict[str, Any]]] = []
    for span in spans:
        if not groups:
            groups.append([span])
            continue
        proposed_start = float(groups[-1][0]["start_sec"]) - 5.0
        proposed_end = float(span["end_sec"]) + 5.0
        if proposed_end - proposed_start <= maximum_window_sec:
            groups[-1].append(span)
        else:
            groups.append([span])
    return groups


def download_candidate(
    row: dict[str, Any], root: Path, discovery: dict[str, Any] | None = None,
    span_group: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    discovery = discovery or row["discovery"]
    media_id = str(discovery.get("media_id") or "")
    media_url = str(discovery.get("media_url") or "")
    if not media_id or not media_url:
        raise RecoveryError("candidate has no media_id/media_url")
    parsed = urllib.parse.urlsplit(media_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RecoveryError("candidate media URL is not HTTPS")
    provider = str(discovery.get("provider") or "")
    original_media_url = media_url
    evidence = discovery.get("evidence") or {}
    reference_url = str(
        evidence.get("archive_page")
        or evidence.get("feed_url")
        or discovery.get("reference_url")
        or row.get("package")
        or ""
    )
    source_offset_sec = 0.0
    if provider == "internet-archive-tvnews":
        spans = span_group or list((discovery.get("evidence") or {}).get("caption_spans") or [])
        if spans:
            source_offset_sec = max(0.0, min(float(value["start_sec"]) for value in spans) - 5.0)
            source_end_sec = max(float(value["end_sec"]) for value in spans) + 5.0
            if source_end_sec - source_offset_sec > 600.0:
                raise RecoveryError("archive package span exceeds the 10-minute bounded-clip limit")
            query = urllib.parse.urlencode(
                {
                    "start": int(math.floor(source_offset_sec)),
                    "end": int(math.ceil(source_end_sec)),
                    "x": "ignore.mp4",
                }
            )
            media_url = media_url + "?" + query
            source_offset_sec = float(math.floor(source_offset_sec))
        elif (
            discovery.get("status") == "candidate_asr_required"
            and evidence.get("access") == "public-search-hit-bounded-asr"
            and evidence.get("asr_hint_start_sec") is not None
            and evidence.get("identifier")
        ):
            hint = float(evidence["asr_hint_start_sec"])
            source_offset_sec = float(max(0, math.floor(hint - 300.0)))
            source_end_sec = float(math.ceil(hint + 300.0))
            query = urllib.parse.urlencode(
                {"start": int(source_offset_sec), "end": int(source_end_sec), "x": "ignore.mp4"}
            )
            media_url = media_url + "?" + query
        elif (
            discovery.get("status") != "candidate_asr_required"
            or evidence.get("access") != "public-full-program-asr"
            or not evidence.get("identifier")
        ):
            raise RecoveryError(
                "unbounded archive acquisition lacks a public media identity for local ASR"
            )
    target_dir = root / safe_media_key(media_id)
    if provider == "internet-archive-tvnews" and (
        spans or evidence.get("access") == "public-search-hit-bounded-asr"
    ):
        target_dir = target_dir / safe_media_key(row["package_id"])
        if span_group is not None:
            tag_start = int(math.floor(min(float(value["start_sec"]) for value in span_group)))
            tag_end = int(math.ceil(max(float(value["end_sec"]) for value in span_group)))
            target_dir = target_dir / f"span_{tag_start}_{tag_end}"
    metadata_path = target_dir / "source.json"
    wav_path = target_dir / "source.wav"
    if metadata_path.exists() and wav_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        digest = sha256_file(wav_path)
        if digest != metadata.get("wav_sha256"):
            raise RecoveryError(f"existing WAV hash mismatch: {wav_path}")
        return metadata
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="speech-source-") as temp_raw:
        temp = Path(temp_raw)
        source = temp / "download"
        if provider == "internet-archive-tvnews":
            # Stream the public archive derivative directly into mono PCM.
            # Saving the video container first would temporarily double disk
            # usage and is unnecessary for an audio-only recovery lane.
            source = Path(media_url)
        elif urllib.parse.urlsplit(media_url).path.lower().endswith(".m3u8"):
            # A saved HLS master contains relative variant/segment paths and is
            # not a self-contained recording.  Let ffmpeg resolve the official
            # remote playlist directly instead of mistaking playlist text for
            # audio bytes.
            source = Path(media_url)
        elif direct_audio_url(media_url):
            request = urllib.request.Request(media_url, headers=UA)
            with urllib.request.urlopen(request, timeout=180) as response, source.open("wb") as handle:
                shutil.copyfileobj(response, handle, 1024 * 1024)
        else:
            command = [
                sys.executable,
                "-m",
                "yt_dlp",
                "--no-playlist",
                "--no-overwrites",
                "--format",
                "bestaudio/best",
                "--output",
                str(temp / "download.%(ext)s"),
                media_url,
            ]
            try:
                process = subprocess.run(
                    command, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=YT_DLP_DOWNLOAD_TIMEOUT_SEC,
                )
            except subprocess.TimeoutExpired as exc:
                raise RecoveryError(
                    "yt-dlp timed out after %ds" % YT_DLP_DOWNLOAD_TIMEOUT_SEC
                ) from exc
            if process.returncode:
                raise RecoveryError("yt-dlp failed: " + process.stderr[-600:])
            downloads = [path for path in temp.glob("download.*") if path.is_file()]
            if len(downloads) != 1:
                raise RecoveryError(f"yt-dlp emitted {len(downloads)} candidate files")
            source = downloads[0]
        if source != Path(media_url) and source.stat().st_size < 4096:
            raise RecoveryError("downloaded media is implausibly small")
        temp_wav = temp / "source.wav"
        command = [
            ffmpeg_path(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            media_url if source == Path(media_url) else str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-threads",
            "1",
            str(temp_wav),
        ]
        try:
            process = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=FFMPEG_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired as exc:
            raise RecoveryError(
                "ffmpeg conversion timed out after %ds" % FFMPEG_TIMEOUT_SEC
            ) from exc
        if process.returncode or not temp_wav.exists():
            raise RecoveryError("ffmpeg conversion failed: " + process.stderr[-600:])
        os.replace(temp_wav, wav_path)
    metadata = {
        "schema": "speech-source-media-acquisition-v1",
        "package_id": row["package_id"],
        "provider": discovery.get("provider"),
        "media_id": media_id,
        "media_url": original_media_url,
        "download_url": media_url,
        "reference_url": reference_url,
        "transcript_package_url": str(row.get("package") or ""),
        "source_offset_sec": source_offset_sec,
        "caption_rowids": [int(value["rowid"]) for value in (span_group or [])],
        "wav": str(wav_path.resolve()),
        "wav_sha256": sha256_file(wav_path),
        "bytes": wav_path.stat().st_size,
        "acquired_at": utc_now(),
    }
    if provider == "internet-archive-tvnews":
        metadata["archive_source"] = {
            "identifier": evidence.get("identifier"),
            "file": evidence.get("source_file"),
            "size": evidence.get("source_size"),
            "md5": evidence.get("source_md5"),
            "sha1": evidence.get("source_sha1"),
            "access": evidence.get("access"),
        }
    write_json_atomic(metadata_path, metadata)
    return metadata


def run_acquire(args: argparse.Namespace) -> int:
    path = Path(args.inventory)
    rows = read_jsonl(path, required=True)
    selected = {value.strip() for value in (args.source or "").split(",") if value.strip()}
    member_targets = load_member_gap(getattr(args, "member_gap", ""))
    eligible: list[dict[str, Any]] = []
    for row in rows:
        if selected and row["source_family"] not in selected:
            continue
        if args.package_id and row["package_id"] != args.package_id:
            continue
        if str((row.get("discovery") or {}).get("status")) not in ACQUIRABLE:
            continue
        if row.get("acquisition", {}).get("status") == "acquired" and not args.refresh:
            continue
        if member_gap_priority(row, member_targets) is None:
            continue
        eligible.append(row)
    eligible.sort(key=lambda row: member_gap_priority(row, member_targets))
    if args.max_packages:
        eligible = eligible[: args.max_packages]

    locks_guard = threading.Lock()
    media_locks: dict[str, threading.Lock] = {}

    def media_lock(media_id: str) -> threading.Lock:
        with locks_guard:
            return media_locks.setdefault(media_id, threading.Lock())

    def acquire_row(row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        acquired: list[dict[str, Any]] = []
        candidate_errors: list[dict[str, str]] = []
        candidates = candidate_discoveries(row)[: args.candidate_limit]
        for candidate in candidates:
            try:
                # Identical CNN hour/program media can serve several transcript
                # packages.  Serialize only that media ID so workers never race
                # the same atomic source/receipt files.
                with media_lock(str(candidate.get("media_id") or row["package_id"])):
                    groups = (caption_span_groups(candidate)
                              if candidate.get("provider") == "internet-archive-tvnews" else [])
                    if len(groups) > 1:
                        for group in groups:
                            acquired.append(download_candidate(
                                row, Path(args.media_root), candidate, span_group=group
                            ))
                    else:
                        acquired.append(download_candidate(row, Path(args.media_root), candidate))
            except (RecoveryError, OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                candidate_errors.append(
                    {"media_id": str(candidate.get("media_id") or ""), "reason": str(exc)[:500]}
                )
        if acquired:
            return {
                "status": "acquired",
                "items": acquired,
                "candidate_errors": candidate_errors,
                "acquired_at": utc_now(),
            }, False
        return {
            "status": "error",
            "candidate_errors": candidate_errors,
            "reason": "no candidate media acquired",
            "attempted_at": utc_now(),
        }, True

    processed = 0
    errors = 0
    workers = max(1, int(args.workers))
    executor = (
        concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        if workers > 1 else None
    )

    def completed_acquisitions() -> Iterator[tuple[dict[str, Any], tuple[dict[str, Any], bool]]]:
        if executor is None:
            for row in eligible:
                yield row, acquire_row(row)
            return
        future_rows = {executor.submit(acquire_row, row): row for row in eligible}
        for future in concurrent.futures.as_completed(future_rows):
            yield future_rows[future], future.result()

    try:
        for row, (acquisition, failed) in completed_acquisitions():
            row["acquisition"] = acquisition
            processed += 1
            errors += int(failed)
            if processed % max(1, args.checkpoint_every) == 0 or processed == len(eligible):
                persist(rows, path)
            print(f"ACQUIRE {row['package_id']} status={acquisition['status']}", flush=True)
    finally:
        if executor is not None:
            executor.shutdown(wait=True)
    print(json.dumps({"processed": processed, "errors": errors, "summary": persist(rows, path)}, sort_keys=True))
    return 2 if errors else 0


def ordered_alignment(
    transcript_tokens: Sequence[str],
    asr_tokens: Sequence[str],
    start_at: int,
    *,
    minimum_ratio: float = 0.68,
) -> tuple[int, int, float] | None:
    """Find one transcript turn in ASR tokens after ``start_at``.

    Candidate windows are anchored on distinctive five-token fragments, then
    scored with an order-sensitive sequence ratio.  The monotonic pointer is
    owned by the caller, so accepted turns cannot overlap or move backwards.
    """
    target = [token for token in transcript_tokens if token]
    if len(target) < 12:
        return None
    maximum_start = max(start_at, len(asr_tokens) - max(8, int(len(target) * 0.55)))
    anchor_len = min(5, len(target))
    anchors = [target[index : index + anchor_len] for index in range(0, min(len(target) - anchor_len + 1, 25), 5)]
    candidate_starts: set[int] = set()
    for anchor in anchors:
        first = anchor[0]
        for index in range(start_at, len(asr_tokens)):
            if asr_tokens[index] != first:
                continue
            probe = asr_tokens[index : index + anchor_len + 3]
            if sum(1 for token in anchor if token in probe) >= max(3, anchor_len - 1):
                candidate_starts.add(max(start_at, index - target.index(anchor[0])))
            if len(candidate_starts) >= 80:
                break
    if not candidate_starts:
        stride = max(1, len(target) // 4)
        candidate_starts.update(range(start_at, maximum_start + 1, stride))
    best: tuple[int, int, float] | None = None
    for begin in sorted(candidate_starts):
        for multiplier in (0.70, 0.85, 1.0, 1.15, 1.30):
            end = min(len(asr_tokens), begin + max(8, int(len(target) * multiplier)))
            if end <= begin:
                continue
            ratio = difflib.SequenceMatcher(None, target, list(asr_tokens[begin:end]), autojunk=False).ratio()
            candidate = (begin, end, ratio)
            if best is None or ratio > best[2]:
                best = candidate
    return best if best and best[2] >= minimum_ratio else None


ASR_WORD_SCHEMA = "speech-source-asr-word-v1"
ASR_RECEIPT_SCHEMA = "speech-source-asr-transcript-receipt-v1"


def _validate_asr_rows(rows: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """Validate a Whisper word stream without treating it as identity evidence."""
    last_start = -1.0
    normalized_count = 0
    for index, row in enumerate(rows):
        if row.get("schema") != ASR_WORD_SCHEMA or int(row.get("index") or 0) != index:
            raise RecoveryError("ASR word stream schema/index mismatch")
        start = float(row.get("start_sec") or 0.0)
        end = float(row.get("end_sec") or 0.0)
        if not math.isfinite(start) or not math.isfinite(end) or start < last_start or end < start:
            raise RecoveryError("ASR word timestamps are not finite and monotonic")
        last_start = start
        normalized_count += len(normalize_tokens(str(row.get("word") or "")))
    if not rows or normalized_count < 20:
        raise RecoveryError("ASR returned too few timed words")
    return len(rows), normalized_count


def transcript_artifact_paths(acquisition: dict[str, Any]) -> tuple[Path, Path]:
    wav_path = Path(str(acquisition.get("wav") or ""))
    if not wav_path.is_absolute():
        raise RecoveryError("acquired WAV path is not absolute")
    explicit_words = acquisition.get("asr_transcript")
    explicit_receipt = acquisition.get("asr_transcript_receipt")
    if explicit_words or explicit_receipt:
        words_path = Path(str(explicit_words or ""))
        receipt_path = Path(str(explicit_receipt or ""))
        if not words_path.is_absolute() or not receipt_path.is_absolute():
            raise RecoveryError("explicit ASR artifact paths must both be absolute")
        if words_path == receipt_path or words_path.parent != receipt_path.parent:
            raise RecoveryError("explicit ASR transcript and receipt must be distinct siblings")
        return words_path, receipt_path
    return wav_path.parent / "transcript.words.jsonl", wav_path.parent / "transcript.receipt.json"


def load_or_create_asr_transcript(
    acquisition: dict[str, Any], model: Any, audio: Any
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Create or verify a hash-bound Whisper word transcript.

    The crawler transcript is aligned to this timing authority later.  Neither
    transcript supplies speaker identity; that is a separate exhaustive anchor
    decision.
    """
    wav_path = Path(str(acquisition.get("wav") or ""))
    source_sha256 = sha256_file(wav_path)
    if source_sha256 != acquisition.get("wav_sha256"):
        raise RecoveryError("acquired WAV hash mismatch before transcription")
    condition_on_previous_text = acquisition.get("condition_on_previous_text", True)
    if not isinstance(condition_on_previous_text, bool):
        raise RecoveryError("condition_on_previous_text must be a boolean")
    requested_settings = acquisition.get("asr_request_settings")
    words_path, receipt_path = transcript_artifact_paths(acquisition)
    if words_path.exists() and receipt_path.exists():
        receipt = _read_json_object(receipt_path)
        rows = read_jsonl(words_path, required=True)
        count, normalized_count = _validate_asr_rows(rows)
        if (
            receipt.get("schema") != ASR_RECEIPT_SCHEMA
            or receipt.get("status") != "PASS"
            or receipt.get("speaker_identity_authority") is not False
            or receipt.get("source_audio_sha256") != source_sha256
            or Path(str(receipt.get("transcript") or "")).resolve() != words_path.resolve()
            or receipt.get("transcript_sha256") != sha256_file(words_path)
            or int(receipt.get("word_count") or -1) != count
            or int(receipt.get("normalized_token_count") or -1) != normalized_count
            or receipt.get("condition_on_previous_text", True) != condition_on_previous_text
            or (requested_settings is not None and receipt.get("asr_request_settings") != requested_settings)
        ):
            raise RecoveryError("stored ASR transcript receipt is stale or hash-invalid")
        return rows, receipt

    segments, info = model.transcribe(
        audio,
        beam_size=1,
        best_of=1,
        temperature=0.0,
        language=(str(acquisition.get("language") or "").strip() or None),
        word_timestamps=True,
        condition_on_previous_text=condition_on_previous_text,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    rows: list[dict[str, Any]] = []
    for segment in segments:
        for word in segment.words or []:
            if not normalize_tokens(str(word.word)):
                continue
            probability = getattr(word, "probability", None)
            rows.append({
                "schema": ASR_WORD_SCHEMA,
                "index": len(rows),
                "word": str(word.word),
                "start_sec": round(float(word.start), 6),
                "end_sec": round(float(word.end), 6),
                "probability": (
                    round(float(probability), 8)
                    if probability is not None and math.isfinite(float(probability))
                    else None
                ),
            })
    count, normalized_count = _validate_asr_rows(rows)
    write_jsonl_atomic(words_path, rows)
    receipt = {
        "schema": ASR_RECEIPT_SCHEMA,
        "status": "PASS",
        "generated_at": utc_now(),
        "source_wav": str(wav_path.resolve()),
        "source_audio_sha256": source_sha256,
        "source_media_url": str(acquisition.get("media_url") or ""),
        "reference_url": str(
            acquisition.get("reference_url")
            or acquisition.get("transcript_package_url")
            or acquisition.get("media_url")
            or ""
        ),
        "timing_authority": "faster-whisper-word-timestamps",
        "speaker_identity_authority": False,
        "crawler_transcript_role": "lexical-alignment-only",
        "model_path": str(Path(str(acquisition.get("model_path") or DEFAULT_MODEL)).resolve()),
        "language": str(getattr(info, "language", "") or ""),
        "condition_on_previous_text": condition_on_previous_text,
        "asr_request_settings": requested_settings,
        "word_count": count,
        "normalized_token_count": normalized_count,
        "transcript": str(words_path.resolve()),
        "transcript_sha256": sha256_file(words_path),
    }
    write_json_atomic(receipt_path, receipt)
    return rows, receipt


def load_turns_for_record(record: dict[str, Any], db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    rowids = [int(rowid) for member in record["members"] for rowid in member["rowids"]]
    if not rowids:
        return []
    placeholders = ",".join("?" for _ in rowids)
    uri = "file:" + db_path.resolve().as_posix() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            f"SELECT rowid,member,date,chamber,package,nwords,text FROM speeches WHERE source='INTERVIEW' AND rowid IN ({placeholders}) ORDER BY rowid",
            rowids,
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "rowid": int(row[0]),
            "member": str(row[1]),
            "date": str(row[2] or ""),
            "show": str(row[3] or ""),
            "package": str(row[4]),
            "words": int(row[5] or 0),
            "text": str(row[6] or ""),
        }
        for row in rows
    ]


def extract_feature_values(features: Any) -> dict[str, float]:
    columns = {
        "f0_mean_semitone": "F0semitoneFrom27.5Hz_sma3nz_amean",
        "pitch_var_norm": "F0semitoneFrom27.5Hz_sma3nz_stddevNorm",
        "loudness_mean": "loudness_sma3_amean",
        "loudness_var_norm": "loudness_sma3_stddevNorm",
        "jitter": "jitterLocal_sma3nz_amean",
        "shimmer": "shimmerLocaldB_sma3nz_amean",
        "hnr_db": "HNRdBACF_sma3nz_amean",
        "voiced_rate": "VoicedSegmentsPerSec",
        "pause_mean_sec": "MeanUnvoicedSegmentLength",
    }
    result: dict[str, float] = {}
    for output, column in columns.items():
        if column not in features.index:
            raise RecoveryError(f"OpenSMILE output is missing: {column}")
        value = float(features[column])
        if not math.isfinite(value):
            raise RecoveryError(f"non-finite acoustic feature: {column}")
        result[output] = round(value, 6)
    return result


def align_record(
    record: dict[str, Any],
    acquisition: dict[str, Any],
    binding_discovery: dict[str, Any],
    model: Any,
    smile: Any,
    output_root: Path,
    *,
    minimum_ratio: float,
) -> list[dict[str, Any]]:
    import numpy as np  # type: ignore
    import soundfile as sf  # type: ignore

    wav_path = Path(str(acquisition.get("wav") or ""))
    if not wav_path.exists():
        raise RecoveryError("acquired WAV is missing")
    digest = sha256_file(wav_path)
    if digest != acquisition.get("wav_sha256"):
        raise RecoveryError("acquired WAV hash mismatch")
    source_offset_sec = float(acquisition.get("source_offset_sec") or 0.0)
    audio, sample_rate = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if getattr(audio, "ndim", 1) == 2:
        audio = audio.mean(axis=1)
    transcript_rows, transcript_receipt = load_or_create_asr_transcript(
        acquisition, model, audio
    )
    package_turn_rowids = sorted(
        int(rowid) for member in record["members"] for rowid in member["rowids"]
    )
    source_expected_turn_rowids = sorted(
        int(value) for value in (acquisition.get("caption_rowids") or package_turn_rowids)
    )
    asr_tokens: list[str] = []
    token_times: list[tuple[float, float]] = []
    for word in transcript_rows:
        tokens = normalize_tokens(str(word["word"]))
        for token in tokens:
            asr_tokens.append(token)
            token_times.append((float(word["start_sec"]), float(word["end_sec"])))
    pointer = 0
    clips: list[dict[str, Any]] = []
    media_key = safe_media_key(str(binding_discovery.get("media_id") or record["package_id"]))
    clip_dir = output_root / media_key / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    for turn in load_turns_for_record(record):
        alignment = ordered_alignment(normalize_tokens(turn["text"]), asr_tokens, pointer, minimum_ratio=minimum_ratio)
        if not alignment:
            continue
        begin, end, ratio = alignment
        start_sec = max(0.0, token_times[begin][0] - 0.12)
        end_sec = min(len(audio) / float(sample_rate), token_times[end - 1][1] + 0.12)
        source_start_frame = seconds_to_frame(start_sec, sample_rate)
        source_end_frame = seconds_to_frame(end_sec, sample_rate)
        source_frame_count = source_end_frame - source_start_frame
        if source_frame_count < sample_rate * 2:
            continue
        pointer = end
        signal = np.asarray(audio[source_start_frame:source_end_frame])
        if signal.size < sample_rate * 2:
            continue
        rowid = int(turn["rowid"])
        clip_key = staged_clip_key(
            digest,
            source_start_frame,
            source_frame_count,
            int(sample_rate),
            f"sqlite:speeches:{rowid}",
            str(transcript_receipt["transcript_sha256"]),
        )[:32]
        clip_path = clip_dir / f"speeches_{rowid}_{clip_key}.wav"
        temp = clip_path.with_suffix(".wav.tmp")
        sf.write(str(temp), signal, sample_rate, format="WAV", subtype="PCM_16")
        os.replace(temp, clip_path)
        source_pcm_sha256 = wav_pcm_slice_sha256(
            wav_path, source_start_frame, source_frame_count, sample_rate=int(sample_rate)
        )
        staged_pcm_sha256 = wav_pcm_slice_sha256(
            clip_path, 0, source_frame_count, sample_rate=int(sample_rate)
        )
        if staged_pcm_sha256 != source_pcm_sha256:
            raise RecoveryError("staged candidate PCM differs from its exact secured source slice")
        features = smile.process_signal(signal, sample_rate).iloc[0]
        source_receipt = Path(str(
            acquisition.get("source_receipt") or (wav_path.parent / "source.json")
        ))
        if not source_receipt.is_file():
            raise RecoveryError("source acquisition receipt is missing")
        clip = {
            "schema": CLIP_SCHEMA,
            "clip_id": f"source-media:{record['package_id']}:{rowid}",
            "member": turn["member"],
            "source_family": record["source_family"],
            "show": turn["show"],
            "date": turn["date"],
            "package": record["package"],
            "turn_id": f"sqlite:speeches:{rowid}",
            "source_expected_turn_rowids": source_expected_turn_rowids,
            "transcript_path": str(DB_PATH.resolve()) + f"#speeches.rowid={rowid}",
            "text_sha256": sha256_text(turn["text"]),
            "source_audio_sha256": digest,
            "source_wav": str(wav_path.resolve()),
            "source_receipt": str(source_receipt.resolve()),
            "source_receipt_sha256": sha256_file(source_receipt),
            "reference_url": str(
                acquisition.get("reference_url")
                or acquisition.get("transcript_package_url")
                or acquisition.get("media_url")
                or record.get("package")
                or ""
            ),
            "asr_transcript": transcript_receipt["transcript"],
            "asr_transcript_sha256": transcript_receipt["transcript_sha256"],
            "asr_transcript_receipt": str(transcript_artifact_paths(acquisition)[1].resolve()),
            "asr_transcript_receipt_sha256": sha256_file(transcript_artifact_paths(acquisition)[1]),
            "provider": binding_discovery.get("provider"),
            "media_id": binding_discovery.get("media_id"),
            "binding_method": "official-media-id+whisper-order-sensitive-monotonic-transcript-alignment",
            "alignment_ratio": round(ratio, 6),
            "start_sec": round(source_start_frame / float(sample_rate) + source_offset_sec, 6),
            "end_sec": round(source_end_frame / float(sample_rate) + source_offset_sec, 6),
            "acquired_clip_start_sec": round(source_start_frame / float(sample_rate), 6),
            "source_offset_sec": round(source_offset_sec, 3),
            "duration_sec": round(source_frame_count / float(sample_rate), 6),
            "source_sample_rate": int(sample_rate),
            "source_start_frame": source_start_frame,
            "source_frame_count": source_frame_count,
            "source_slice_pcm_sha256": source_pcm_sha256,
            "staged_pcm_sha256": staged_pcm_sha256,
            "words": turn["words"],
            "wav": str(clip_path.resolve()),
            "audio_sha256": sha256_file(clip_path),
            "sample_rate": int(sample_rate),
            "status": "CANDIDATE_AUDIT_REQUIRED",
            "speaker_identity_status": "EXHAUSTIVE_MULTI_ANCHOR_REQUIRED",
            "features": extract_feature_values(features),
        }
        clip["cut_binding_sha256"] = candidate_cut_binding(clip)
        clip["clip_sha256"] = sha256_json(clip)
        clips.append(clip)
    return clips


def run_align(args: argparse.Namespace) -> int:
    # PyAV's native FIFO extension is blocked by the host application-control
    # policy.  faster-whisper needs only the exception type because this lane
    # supplies already-decoded numpy audio through soundfile.  This is the same
    # proven shim used by axe_segment.py.
    if "av" not in sys.modules:
        av_module = types.ModuleType("av")
        av_module.audio = types.ModuleType("av.audio")
        av_module.error = types.ModuleType("av.error")

        class InvalidDataError(Exception):
            pass

        av_module.error.InvalidDataError = InvalidDataError
        av_module.open = None
        sys.modules["av"] = av_module
        sys.modules["av.audio"] = av_module.audio
        sys.modules["av.error"] = av_module.error
    try:
        from faster_whisper import WhisperModel  # type: ignore
        import opensmile  # type: ignore
    except ImportError as exc:
        raise RecoveryError(f"alignment dependency is missing: {exc}") from exc
    rows = read_jsonl(Path(args.inventory), required=True)
    selected = {value.strip() for value in (args.source or "").split(",") if value.strip()}
    member_targets = load_member_gap(getattr(args, "member_gap", ""))
    output_root = Path(args.media_root)
    model = WhisperModel(
        str(Path(args.model)),
        device="cpu",
        compute_type="int8",
        local_files_only=True,
        cpu_threads=max(1, args.cpu_threads),
        num_workers=max(1, int(args.workers)),
    )
    thread_state = threading.local()
    source_locks_guard = threading.Lock()
    source_locks: dict[str, threading.Lock] = {}

    def source_lock(acquisition: dict[str, Any]) -> threading.Lock:
        # Several crawler packages can point at the same physical broadcast.
        # Serialize that WAV's transcript/cut writes so parallel packages reuse
        # one hash-bound Whisper artifact instead of racing duplicate writers.
        key = os.path.normcase(str(Path(str(acquisition.get("wav") or "")).resolve()))
        with source_locks_guard:
            return source_locks.setdefault(key, threading.Lock())

    def thread_smile() -> Any:
        value = getattr(thread_state, "smile", None)
        if value is None:
            value = opensmile.Smile(
                feature_set=opensmile.FeatureSet.eGeMAPSv02,
                feature_level=opensmile.FeatureLevel.Functionals,
            )
            thread_state.smile = value
        return value

    manifest_path = output_root / "candidate_clips.jsonl"
    existing = {row["clip_id"]: row for row in read_jsonl(manifest_path)}
    eligible: list[dict[str, Any]] = []
    for row in rows:
        if selected and row["source_family"] not in selected:
            continue
        if args.package_id and row["package_id"] != args.package_id:
            continue
        if (row.get("acquisition") or {}).get("status") != "acquired":
            continue
        alignment = row.get("alignment") or {}
        if not args.refresh:
            marker_current = (
                alignment.get("clip_schema") == CLIP_SCHEMA
                and alignment.get("exact_frame_cut_binding") is True
            )
            package_clips = [
                clip for clip in existing.values()
                if str(clip.get("package") or "") == str(row.get("package") or "")
                and str(clip.get("source_family") or "") == str(row.get("source_family") or "")
            ]
            checkpoint_current = (
                alignment.get("status") == "candidate_clips_emitted"
                and len(package_clips) == int(alignment.get("candidate_clips") or -1)
                and bool(package_clips)
                and all(
                    clip.get("schema") == CLIP_SCHEMA
                    and clip.get("cut_binding_sha256") == candidate_cut_binding(clip)
                    and clip.get("clip_sha256") == _candidate_hash(clip)
                    for clip in package_clips
                )
            )
            zero_candidate_current = (
                marker_current
                and alignment.get("status") == "no_member_turn_matched"
                and int(alignment.get("candidate_clips") or 0) == 0
                and not package_clips
            )
            if checkpoint_current or zero_candidate_current:
                continue
        if member_gap_priority(row, member_targets) is None:
            continue
        eligible.append(row)
    eligible.sort(key=lambda row: member_gap_priority(row, member_targets))
    if args.max_packages:
        eligible = eligible[: args.max_packages]

    def align_one(row: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
        candidates = {str(value.get("media_id")): value for value in candidate_discoveries(row)}
        items = list((row.get("acquisition") or {}).get("items") or [])
        if not items and (row.get("acquisition") or {}).get("wav"):
            items = [row["acquisition"]]
        clips_by_id: dict[str, dict[str, Any]] = {}
        tried: list[dict[str, Any]] = []
        item_errors: list[dict[str, Any]] = []
        for acquisition in items:
            media_id = str(acquisition.get("media_id") or "")
            binding = candidates.get(media_id) or {
                "media_id": media_id,
                "provider": acquisition.get("provider"),
            }
            try:
                with source_lock(acquisition):
                    candidate_clips = align_record(
                        row,
                        acquisition,
                        binding,
                        model,
                        thread_smile(),
                        output_root,
                        minimum_ratio=args.minimum_ratio,
                    )
            except (RecoveryError, OSError, sqlite3.Error) as exc:
                if str(exc) == "ASR returned too few timed words":
                    # A short/silent bounded recording cannot support lexical
                    # localization or the three-window identity floor.  After
                    # a completed ASR attempt this is a terminal unresolved
                    # disposition, not an infrastructure failure to retry.
                    tried.append({
                        "media_id": media_id,
                        "wav": str(acquisition.get("wav") or ""),
                        "reason": str(exc),
                        "status": "no_speech_or_insufficient_asr",
                        "candidate_clips": 0,
                    })
                    continue
                failure = {
                    "media_id": media_id,
                    "wav": str(acquisition.get("wav") or ""),
                    "reason": str(exc)[:500],
                }
                item_errors.append(failure)
                tried.append({**failure, "status": "error", "candidate_clips": 0})
                continue
            for clip in candidate_clips:
                prior = clips_by_id.get(clip["clip_id"])
                if prior is None or float(clip["alignment_ratio"]) > float(prior["alignment_ratio"]):
                    clips_by_id[clip["clip_id"]] = clip
            tried.append({
                "media_id": media_id,
                "status": "aligned",
                "candidate_clips": len(candidate_clips),
            })
        clips = list(clips_by_id.values())
        if clips:
            status = "candidate_clips_emitted"
            failed = False
        elif item_errors:
            status = "error"
            failed = True
        else:
            status = "no_member_turn_matched"
            failed = False
        alignment = {
            "status": status,
            "candidate_clips": len(clips),
            "candidates_tried": tried,
            "item_errors": item_errors,
            "minimum_ratio": args.minimum_ratio,
            "clip_schema": CLIP_SCHEMA,
            "exact_frame_cut_binding": True,
            "aligned_at": utc_now(),
        }
        return clips, alignment, failed

    processed = 0
    failures = 0
    workers = max(1, int(args.workers))
    executor = (
        concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        if workers > 1 else None
    )

    def completed_alignments() -> Iterator[
        tuple[dict[str, Any], tuple[list[dict[str, Any]], dict[str, Any], bool]]
    ]:
        if executor is None:
            for row in eligible:
                yield row, align_one(row)
            return
        future_rows = {executor.submit(align_one, row): row for row in eligible}
        for future in concurrent.futures.as_completed(future_rows):
            # The consumer loop's own except clause cannot see an exception raised here, so
            # any failure align_one did not already catch aborted the entire run and lost
            # every package after the last checkpoint. Record it the way the consumer would.
            try:
                result = future.result()
            except (RecoveryError, OSError, sqlite3.Error) as exc:
                result = ([], {"status": "error", "reason": str(exc)[:500],
                               "attempted_at": utc_now()}, True)
            yield future_rows[future], result

    try:
        for row, result in completed_alignments():
            try:
                clips, alignment, failed = result
                stale_clip_ids = [
                    clip_id for clip_id, clip in existing.items()
                    if str(clip.get("package") or "") == str(row.get("package") or "")
                    and str(clip.get("source_family") or "") == str(row.get("source_family") or "")
                ]
                for clip_id in stale_clip_ids:
                    existing.pop(clip_id, None)
                for clip in clips:
                    existing[clip["clip_id"]] = clip
                row["alignment"] = alignment
                failures += int(failed)
            except (RecoveryError, OSError, sqlite3.Error) as exc:
                row["alignment"] = {"status": "error", "reason": str(exc)[:500], "attempted_at": utc_now()}
                failures += 1
            processed += 1
            if processed % max(1, args.checkpoint_every) == 0 or processed == len(eligible):
                write_jsonl_atomic(manifest_path, sorted(existing.values(), key=lambda value: value["clip_id"]))
                persist(rows, Path(args.inventory))
            print(
                f"ALIGN {row['package_id']} status={row['alignment']['status']} clips={row['alignment'].get('candidate_clips', 0)}",
                flush=True,
            )
    finally:
        if executor is not None:
            executor.shutdown(wait=True)
    schema_counts = collections.Counter(
        str(row.get("schema") or "missing") for row in existing.values()
    )
    exact_frame_candidates = sum(
        row.get("schema") == CLIP_SCHEMA
        and row.get("cut_binding_sha256") == candidate_cut_binding(row)
        and row.get("clip_sha256") == _candidate_hash(row)
        for row in existing.values()
    )
    receipt = {
        "schema": CANDIDATE_RECEIPT_SCHEMA,
        "generated_at": utc_now(),
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": sha256_file(manifest_path) if manifest_path.exists() else None,
        "candidate_clips": len(existing),
        "clip_schema": CLIP_SCHEMA if exact_frame_candidates == len(existing) else "mixed",
        "clip_schema_counts": dict(sorted(schema_counts.items())),
        "exact_frame_candidates": exact_frame_candidates,
        "inventory": str(Path(args.inventory).resolve()),
        "inventory_sha256": sha256_file(Path(args.inventory)),
        "exact_frame_cut_binding_required": True,
        "status": "AUDIT_REQUIRED",
        "accepted_manifest_modified": False,
    }
    write_json_atomic(output_root / "candidate_clips.receipt.json", receipt)
    print(json.dumps({"processed": processed, "failures": failures, **receipt}, sort_keys=True))
    return 2 if failures else 0


REQUIRED_AUDIT_FEATURES = {
    "f0_mean_semitone",
    "pitch_var_norm",
    "loudness_mean",
    "loudness_var_norm",
    "jitter",
    "shimmer",
    "hnr_db",
    "voiced_rate",
    "pause_mean_sec",
}


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"invalid or missing JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RecoveryError(f"JSON root is not an object: {path}")
    return value


def _roster_names(path: Path) -> set[str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"invalid or missing roster: {path}") from exc
    if not isinstance(value, list):
        raise RecoveryError("roster root is not a list")
    names = {str(row.get("member") or "") for row in value if isinstance(row, dict)}
    if not names:
        raise RecoveryError("roster has no member names")
    return names


def _candidate_hash(candidate: dict[str, Any]) -> str:
    body = dict(candidate)
    body.pop("clip_sha256", None)
    return sha256_json(body)


def candidate_cut_binding(candidate: dict[str, Any]) -> str:
    """Bind identity audio to one exact hash-pinned source PCM frame range."""
    fields = (
        "source_audio_sha256",
        "source_receipt_sha256",
        "asr_transcript_sha256",
        "asr_transcript_receipt_sha256",
        "source_sample_rate",
        "source_start_frame",
        "source_frame_count",
        "source_slice_pcm_sha256",
        "staged_pcm_sha256",
        "audio_sha256",
        "turn_id",
    )
    body = {field: candidate.get(field) for field in fields}
    return sha256_json(body)


def _supplement_hash(row: dict[str, Any]) -> str:
    body = dict(row)
    body.pop("supplement_row_sha256", None)
    return sha256_json(body)


def _turn_for_candidate(candidate: dict[str, Any], db_path: Path) -> dict[str, Any]:
    match = re.fullmatch(r"sqlite:speeches:(\d+)", str(candidate.get("turn_id") or ""))
    if not match:
        raise RecoveryError("candidate turn_id is not an exact speeches rowid")
    rowid = int(match.group(1))
    uri = "file:" + db_path.resolve().as_posix() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        row = con.execute(
            "SELECT rowid,member,date,chamber,package,nwords,text,source "
            "FROM speeches WHERE rowid=?",
            (rowid,),
        ).fetchone()
    finally:
        con.close()
    if not row or str(row[7]) != "INTERVIEW":
        raise RecoveryError("candidate turn is not a current INTERVIEW row")
    return {
        "rowid": int(row[0]),
        "member": str(row[1]),
        "date": str(row[2] or ""),
        "show": str(row[3] or ""),
        "package": str(row[4]),
        "words": int(row[5] or 0),
        "text": str(row[6] or ""),
    }


def _binding_evidence(
    candidate: dict[str, Any], inventory_by_package: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    package = str(candidate.get("package") or "")
    record = inventory_by_package.get(package)
    if not record:
        raise RecoveryError("candidate package is absent from the bound source-media inventory")
    if record.get("package_id") != package_id(package):
        raise RecoveryError("inventory package_id does not match the candidate package")
    provenance = bound_candidate_provenance(record, candidate)
    return {
        "inventory_package_id": record["package_id"],
        "inventory_row_sha256": sha256_json(record),
        "provenance_kind": provenance["kind"],
        "provenance_evidence_sha256": provenance["binding_sha256"],
        "candidate_provenance_pass": True,
        "program_airtime_used_for_identity": False,
    }


def audit_candidate(
    candidate: dict[str, Any], *, db_path: Path, roster: set[str],
    inventory_by_package: dict[str, dict[str, Any]], minimum_ratio: float,
    verify_audio_hash: bool = True,
) -> dict[str, Any]:
    if candidate.get("schema") != CLIP_SCHEMA or candidate.get("status") != "CANDIDATE_AUDIT_REQUIRED":
        raise RecoveryError("candidate schema/status is not audit eligible")
    if candidate.get("clip_sha256") != _candidate_hash(candidate):
        raise RecoveryError("candidate row hash mismatch")
    turn = _turn_for_candidate(candidate, db_path)
    for field in ("member", "date", "show", "package", "words"):
        if candidate.get(field) != turn[field]:
            raise RecoveryError(f"candidate does not match current SQLite turn field: {field}")
    if candidate["member"] not in roster:
        raise RecoveryError("candidate member is outside the current Moneyball roster")
    if candidate.get("text_sha256") != sha256_text(turn["text"]):
        raise RecoveryError("candidate transcript hash does not match the current SQLite turn")
    if float(candidate.get("alignment_ratio") or 0) < minimum_ratio:
        raise RecoveryError("candidate transcript-to-audio alignment is below the audit threshold")
    if not str(candidate.get("provider") or "") or not str(candidate.get("media_id") or ""):
        raise RecoveryError("candidate lacks provider/media identity")
    binding = _binding_evidence(candidate, inventory_by_package)

    path = Path(str(candidate.get("wav") or ""))
    if not path.is_absolute() or not path.is_file():
        raise RecoveryError("candidate WAV is missing or is not an absolute path")
    with wave.open(str(path), "rb") as handle:
        channels, rate, width, frames = (
            handle.getnchannels(), handle.getframerate(), handle.getsampwidth(), handle.getnframes()
        )
    if (channels, rate, width) != (1, 16000, 2):
        raise RecoveryError("candidate WAV is not mono 16-kHz PCM16")
    duration = frames / 16000.0
    if abs(duration - float(candidate.get("duration_sec") or 0)) > 0.02:
        raise RecoveryError("candidate WAV frame duration does not match the candidate span")
    if abs((float(candidate.get("end_sec") or 0) - float(candidate.get("start_sec") or 0)) - duration) > 0.02:
        raise RecoveryError("candidate source boundaries do not match the cut duration")
    digest = sha256_file(path)
    if verify_audio_hash and digest != candidate.get("audio_sha256"):
        raise RecoveryError("candidate WAV SHA256 mismatch")
    if len(str(candidate.get("source_audio_sha256") or "")) != 64:
        raise RecoveryError("candidate lacks a source-recording SHA256")
    features = candidate.get("features") or {}
    if set(features) != REQUIRED_AUDIT_FEATURES:
        raise RecoveryError("candidate acoustic feature contract is incomplete")
    if any(not math.isfinite(float(features[name])) for name in REQUIRED_AUDIT_FEATURES):
        raise RecoveryError("candidate contains a non-finite acoustic measurement")

    accepted = dict(candidate)
    accepted["schema"] = SUPPLEMENT_SCHEMA
    accepted["status"] = "PROVENANCE_VERIFIED_SUPPLEMENTAL_DISPLAY_ONLY_NO_IDENTITY"
    accepted["audit"] = {
        **binding,
        "database_rowid": turn["rowid"],
        "wav_frames": frames,
        "wav_sha256": digest,
        "minimum_alignment_ratio": minimum_ratio,
        "transcript_audio_alignment_required": True,
        "program_airtime_used_for_identity": False,
        "identity_accepted": False,
        "promotable": False,
        "exhaustive_voice_identity_required_before_promotion": True,
    }
    accepted["candidate_clip_sha256"] = candidate["clip_sha256"]
    accepted.pop("clip_sha256", None)
    accepted["supplement_row_sha256"] = _supplement_hash(accepted)
    return accepted


def run_audit(args: argparse.Namespace) -> int:
    candidate_path = Path(args.candidate_manifest)
    candidate_receipt_path = Path(args.candidate_receipt)
    candidate_receipt = _read_json_object(candidate_receipt_path)
    candidates = read_jsonl(candidate_path, required=True)
    if candidate_receipt.get("schema") != CANDIDATE_RECEIPT_SCHEMA:
        raise RecoveryError("candidate receipt schema mismatch")
    if candidate_receipt.get("manifest_sha256") != sha256_file(candidate_path):
        raise RecoveryError("candidate receipt is not bound to the current candidate manifest")
    if int(candidate_receipt.get("candidate_clips") or -1) != len(candidates):
        raise RecoveryError("candidate receipt row count mismatch")
    inventory_rows = read_jsonl(Path(args.inventory), required=True)
    inventory_by_package = {str(row.get("package") or ""): row for row in inventory_rows}
    roster = _roster_names(Path(args.roster))
    selected = {value.strip() for value in (args.source or "").split(",") if value.strip()}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        if selected and str(candidate.get("source_family") or "") not in selected:
            continue
        if args.package_id and package_id(str(candidate.get("package") or "")) != args.package_id:
            continue
        try:
            accepted.append(audit_candidate(
                candidate,
                db_path=Path(args.db),
                roster=roster,
                inventory_by_package=inventory_by_package,
                minimum_ratio=args.minimum_ratio,
                verify_audio_hash=not args.no_verify_audio_hashes,
            ))
        except (RecoveryError, OSError, sqlite3.Error, ValueError) as exc:
            rejected.append({
                "clip_id": str(candidate.get("clip_id") or ""),
                "candidate_clip_sha256": str(candidate.get("clip_sha256") or ""),
                "reason": str(exc)[:500],
            })
    accepted.sort(key=lambda row: str(row["clip_id"]))
    supplement_path = Path(args.supplement_manifest)
    write_jsonl_atomic(supplement_path, accepted)
    receipt = {
        "schema": "speech-source-media-supplement-receipt-v1",
        "status": "PASS_NON_PROMOTING",
        "generated_at": utc_now(),
        "candidate_manifest": str(candidate_path.resolve()),
        "candidate_manifest_sha256": sha256_file(candidate_path),
        "candidate_receipt": str(candidate_receipt_path.resolve()),
        "candidate_receipt_sha256": sha256_file(candidate_receipt_path),
        "inventory": str(Path(args.inventory).resolve()),
        "inventory_sha256": sha256_file(Path(args.inventory)),
        "database": str(Path(args.db).resolve()),
        "database_sha256": sha256_file(Path(args.db)),
        "roster": str(Path(args.roster).resolve()),
        "roster_sha256": sha256_file(Path(args.roster)),
        "manifest": str(supplement_path.resolve()),
        "manifest_sha256": sha256_file(supplement_path),
        "audited_candidates": len(accepted) + len(rejected),
        "accepted_clips": 0,
        "provenance_verified_clips": len(accepted),
        "rejected_clips": len(rejected),
        "rejections": rejected,
        "contract": {
            "display_only": True,
            "transcript_audio_alignment_required": True,
            "program_airtime_used_for_identity": False,
            "identity_accepted": False,
            "promotable": False,
            "exhaustive_voice_identity_required_before_promotion": True,
            "canonical_manifest_modified": False,
        },
    }
    write_json_atomic(Path(args.supplement_receipt), receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


def _validated_inventory_input(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = read_jsonl(path, required=True)
    _, receipt_path = inventory_sidecars(path)
    receipt = _read_json_object(receipt_path)
    if (
        receipt.get("schema") != "speech-source-media-inventory-receipt-v1"
        or Path(str(receipt.get("inventory_path") or "")).resolve() != path.resolve()
        or receipt.get("inventory_sha256") != sha256_file(path)
        or int(receipt.get("packages") or -1) != len(rows)
    ):
        raise RecoveryError("local reuse input is not bound to the current inventory receipt")
    return rows, receipt


def validate_cnn_local_reuse_inventory_binding(
    routes: Sequence[dict[str, Any]], inventory: Sequence[dict[str, Any]],
    *, inventory_file_sha256: str,
) -> None:
    by_id: dict[str, dict[str, Any]] = {}
    for record in inventory:
        package_key = str(record.get("package_id") or "")
        if package_key in by_id:
            raise RecoveryError(f"current inventory has duplicate package_id: {package_key}")
        by_id[package_key] = record
    for source in routes:
        provenance = source.get("collection_provenance") or {}
        if provenance.get("inventory_file_sha256") != inventory_file_sha256:
            raise RecoveryError("local reuse route is stale against the current inventory file")
        for route in source.get("package_routes") or []:
            package_key = str(route.get("package_id") or "")
            record = by_id.get(package_key)
            if not record:
                raise RecoveryError(f"local reuse package disappeared from inventory: {package_key}")
            if (
                record.get("schema") != SCHEMA
                or record.get("package") != route.get("package")
                or record.get("inventory_sha256") != route.get("inventory_row_sha256")
                or record.get("transcript_sha256") != route.get("crawler_transcript_sha256")
            ):
                raise RecoveryError(f"local reuse package binding is stale: {package_key}")
            basis = str(route.get("localization_routing_basis") or "")
            route_key = route.get("localization_route_key") or {}
            if basis == "candidate-localization:date+show":
                if not (
                    record.get("source_family") == "cnn"
                    and str(record.get("show") or "").lower() == str(route_key.get("show") or "").lower()
                    and str(record.get("date") or "") == str(route_key.get("date") or "")
                ):
                    raise RecoveryError(f"SOTU localization route no longer matches inventory: {package_key}")
            elif basis == "candidate-localization:exact-episode-guid":
                if not (
                    record.get("source_family") == "cnn-axe-files"
                    and _episode_guid(str(record.get("package") or ""))
                    == str(route_key.get("episode_guid") or "").lower()
                ):
                    raise RecoveryError(f"Axe GUID localization route no longer matches inventory: {package_key}")
            else:
                raise RecoveryError(f"unknown local reuse localization route basis: {basis}")


def verify_cnn_local_reuse_source_facts(rows: Sequence[dict[str, Any]]) -> None:
    for row in rows:
        stored = row.get("source") or {}
        current = wav_full_facts(Path(str(stored.get("wav") or "")))
        if current != stored:
            raise RecoveryError(
                f"local reuse source facts changed: {row.get('physical_source_id') or ''}"
            )


def transcribe_cnn_local_reuse_sources(
    rows: Sequence[dict[str, Any]], model: Any, *, model_path: Path,
    audio_loader: Callable[[Path], Any] | None = None,
    checkpoint: Callable[[list[dict[str, Any]]], None] | None = None,
    expected_sources: int = 38, expected_package_routes: int = 42,
    workers: int = 1,
) -> list[dict[str, Any]]:
    """ASR each physical source once with coordinator-only checkpoints.

    Worker threads may write only their unique source-level ASR artifacts.  The
    calling thread alone merges completed rows and invokes ``checkpoint`` so a
    concurrent model call can never publish a manifest or corpus-level receipt.
    """
    validate_cnn_local_reuse_routes(
        rows, expected_sources=expected_sources,
        expected_package_routes=expected_package_routes, require_asr=False,
    )
    if audio_loader is None:
        try:
            import soundfile as sf  # type: ignore
        except ImportError as exc:
            raise RecoveryError(f"local reuse ASR dependency is missing: {exc}") from exc

        def audio_loader(path: Path) -> Any:
            audio, rate = sf.read(str(path), dtype="float32", always_2d=False)
            if int(rate) != 16000:
                raise RecoveryError("local reuse ASR source rate is not 16 kHz")
            if getattr(audio, "ndim", 1) != 1:
                raise RecoveryError("local reuse ASR source is not mono")
            return audio

    updated_by_id = {
        str(row["physical_source_id"]): json.loads(json.dumps(row)) for row in rows
    }
    pending_rows = sorted(
        (
            json.loads(json.dumps(row)) for row in rows
            if (row.get("asr") or {}).get("status") != "PASS"
        ),
        key=lambda item: str(item["physical_source_id"]),
    )

    def transcribe_one(row: dict[str, Any]) -> dict[str, Any]:
        source = row["source"]
        asr = row["asr"]
        acquisition = {
            "wav": source["wav"],
            "wav_sha256": source["source_audio_sha256"],
            "reference_url": row["reference_url"],
            "media_url": row["reference_url"],
            "asr_transcript": asr["transcript"],
            "asr_transcript_receipt": asr["receipt"],
            "model_path": str(model_path.resolve()),
        }
        audio = audio_loader(Path(source["wav"]))
        _, transcript_receipt = load_or_create_asr_transcript(acquisition, model, audio)
        words_path, receipt_path = transcript_artifact_paths(acquisition)
        if (
            transcript_receipt.get("source_audio_sha256") != source["source_audio_sha256"]
            or transcript_receipt.get("speaker_identity_authority") is not False
        ):
            raise RecoveryError("local reuse ASR receipt is not source-bound and non-identifying")
        row["asr"] = {
            **asr,
            "status": "PASS",
            "transcript_sha256": sha256_file(words_path),
            "receipt_sha256": sha256_file(receipt_path),
            "word_count": int(transcript_receipt.get("word_count") or 0),
            "normalized_token_count": int(transcript_receipt.get("normalized_token_count") or 0),
            "source_audio_sha256": source["source_audio_sha256"],
        }
        row["status"] = "ASR_READY_LOCALIZATION_ONLY"
        row["route_row_sha256"] = _route_row_hash(row)
        return row

    limit = min(max(1, int(workers)), max(1, len(pending_rows)))
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=limit, thread_name_prefix="cnn-local-reuse-asr"
    )
    futures: dict[concurrent.futures.Future[dict[str, Any]], str] = {}
    pending_iterator = iter(pending_rows)
    failed = False

    def submit_one() -> bool:
        try:
            row = next(pending_iterator)
        except StopIteration:
            return False
        source_id = str(row["physical_source_id"])
        futures[executor.submit(transcribe_one, row)] = source_id
        return True

    try:
        for _ in range(limit):
            if not submit_one():
                break
        while futures:
            completed, _ = concurrent.futures.wait(
                futures, return_when=concurrent.futures.FIRST_COMPLETED
            )
            batch: list[dict[str, Any]] = []
            failures: list[tuple[str, BaseException]] = []
            for future in completed:
                source_id = futures.pop(future)
                try:
                    batch.append(future.result())
                except BaseException as exc:
                    failures.append((source_id, exc))
            if failures:
                failed = True
                for future in futures:
                    future.cancel()
                source_id, exc = sorted(failures, key=lambda item: item[0])[0]
                if isinstance(exc, RecoveryError):
                    raise RecoveryError(
                        f"local reuse ASR failed for {source_id}: {exc}"
                    ) from exc
                raise RecoveryError(
                    f"local reuse ASR failed for {source_id}: {type(exc).__name__}: {exc}"
                ) from exc
            # A wait may return several simultaneous completions.  Merge them
            # in source-ID order so checkpoint history is deterministic even
            # though model calls complete out of order.
            for completed_row in sorted(
                batch, key=lambda item: str(item["physical_source_id"])
            ):
                source_id = str(completed_row["physical_source_id"])
                updated_by_id[source_id] = completed_row
                current = [updated_by_id[key] for key in sorted(updated_by_id)]
                if checkpoint is not None:
                    checkpoint(current)
                submit_one()
    except BaseException:
        failed = True
        for future in futures:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=failed)

    updated = [updated_by_id[key] for key in sorted(updated_by_id)]
    validate_cnn_local_reuse_routes(
        updated, expected_sources=expected_sources,
        expected_package_routes=expected_package_routes, require_asr=True,
    )
    return updated


def cnn_local_reuse_receipt(
    rows: Sequence[dict[str, Any]], unresolved_axe: Sequence[dict[str, str]], *,
    manifest_path: Path, inventory_path: Path, input_paths: dict[str, Path],
) -> dict[str, Any]:
    counts = validate_cnn_local_reuse_routes(rows, require_asr=False)
    sotu_sources = sum(row.get("source_kind") == "sotu-podcast" for row in rows)
    axe_sources = sum(row.get("source_kind") == "axe-files-podcast" for row in rows)
    return {
        "schema": LOCAL_REUSE_RECEIPT_SCHEMA,
        "status": (
            "PASS_ASR_READY_LOCALIZATION_ONLY"
            if counts["asr_sources_pending"] == 0
            else "PASS_ROUTED_ASR_PENDING_LOCALIZATION_ONLY"
        ),
        "generated_at": utc_now(),
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "inventory": str(inventory_path.resolve()),
        "inventory_sha256": sha256_file(inventory_path),
        "inputs": {
            key: {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for key, path in sorted(input_paths.items())
        },
        **counts,
        "sotu_physical_sources": sotu_sources,
        "sotu_package_routes": sum(
            len(row["package_routes"]) for row in rows if row["source_kind"] == "sotu-podcast"
        ),
        "axe_physical_sources": axe_sources,
        "axe_package_routes": sum(
            len(row["package_routes"])
            for row in rows if row["source_kind"] == "axe-files-podcast"
        ),
        "unresolved_axe_packages": list(unresolved_axe),
        "contract": {
            "candidate_localization_only": True,
            "one_asr_receipt_per_physical_source": True,
            "date_show_guid_title_member_airtime_identity_authority": False,
            "discarded_identity_hints": [
                "sotu_targets.title", "sotu_targets.members",
                "axe_manifest.title", "axe_manifest.member", "axe_manifest.pubDate",
            ],
            "transcript_audio_alignment_required": True,
            "exhaustive_voice_consensus_required_before_identity": True,
            "identity_accepted": False,
            "promotable": False,
            "cleanup_authorized": False,
            "moneyball_modified": False,
        },
    }


def _validate_local_reuse_receipt(path: Path, manifest_path: Path) -> dict[str, Any]:
    receipt = _read_json_object(path)
    contract = receipt.get("contract") or {}
    inventory_path = Path(str(receipt.get("inventory") or ""))
    if (
        receipt.get("schema") != LOCAL_REUSE_RECEIPT_SCHEMA
        or Path(str(receipt.get("manifest") or "")).resolve() != manifest_path.resolve()
        or receipt.get("manifest_sha256") != sha256_file(manifest_path)
        or int(receipt.get("physical_sources") or -1) != 38
        or int(receipt.get("package_routes") or -1) != 42
        or contract.get("candidate_localization_only") is not True
        or contract.get("one_asr_receipt_per_physical_source") is not True
        or contract.get("date_show_guid_title_member_airtime_identity_authority") is not False
        or contract.get("identity_accepted") is not False
        or contract.get("promotable") is not False
        or contract.get("cleanup_authorized") is not False
        or contract.get("moneyball_modified") is not False
        or len(receipt.get("unresolved_axe_packages") or []) != 1
        or not inventory_path.is_file()
        or receipt.get("inventory_sha256") != sha256_file(inventory_path)
    ):
        raise RecoveryError("local reuse receipt is stale or unsafe")
    inputs = receipt.get("inputs") or {}
    if set(inputs) != {"sotu_targets", "sotu_fetch_log", "axe_manifest", "axe_fetch_log"}:
        raise RecoveryError("local reuse receipt input set is incomplete")
    for value in inputs.values():
        input_path = Path(str((value or {}).get("path") or ""))
        if not input_path.is_file() or (value or {}).get("sha256") != sha256_file(input_path):
            raise RecoveryError("local reuse receipt input hash is stale")
    return receipt


def run_local_reuse_route(args: argparse.Namespace) -> int:
    inventory_path = Path(args.inventory)
    inventory, _ = _validated_inventory_input(inventory_path)
    inventory_digest = sha256_file(inventory_path)
    paths = {
        "sotu_targets": Path(args.sotu_targets),
        "sotu_fetch_log": Path(args.sotu_fetch_log),
        "axe_manifest": Path(args.axe_manifest),
        "axe_fetch_log": Path(args.axe_fetch_log),
    }
    input_hashes = {key: sha256_file(path) for key, path in paths.items()}
    input_hashes["inventory"] = inventory_digest
    rows, unresolved = build_cnn_local_reuse_routes(
        inventory,
        _read_json_array(paths["sotu_targets"]),
        read_jsonl(paths["sotu_fetch_log"], required=True),
        read_jsonl(paths["axe_manifest"], required=True),
        read_jsonl(paths["axe_fetch_log"], required=True),
        sotu_root=Path(args.sotu_root),
        asr_root=Path(args.local_reuse_asr_root),
        input_hashes=input_hashes,
    )
    if sha256_file(inventory_path) != inventory_digest:
        raise RecoveryError("inventory changed while local reuse routes were being validated")
    manifest_path = Path(args.local_reuse_manifest)
    write_jsonl_atomic(manifest_path, rows)
    receipt = cnn_local_reuse_receipt(
        rows, unresolved, manifest_path=manifest_path,
        inventory_path=inventory_path, input_paths=paths,
    )
    if receipt.get("inventory_sha256") != inventory_digest:
        raise RecoveryError("inventory changed before the local reuse receipt was committed")
    write_json_atomic(Path(args.local_reuse_receipt), receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


def run_local_reuse_asr(args: argparse.Namespace) -> int:
    # Match run_align's host-policy shim while using only already-decoded PCM.
    if "av" not in sys.modules:
        av_module = types.ModuleType("av")
        av_module.audio = types.ModuleType("av.audio")
        av_module.error = types.ModuleType("av.error")

        class InvalidDataError(Exception):
            pass

        av_module.error.InvalidDataError = InvalidDataError
        av_module.open = None
        sys.modules["av"] = av_module
        sys.modules["av.audio"] = av_module.audio
        sys.modules["av.error"] = av_module.error
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise RecoveryError(f"local reuse ASR dependency is missing: {exc}") from exc
    inventory_path = Path(args.inventory)
    inventory, _ = _validated_inventory_input(inventory_path)
    manifest_path = Path(args.local_reuse_manifest)
    receipt_path = Path(args.local_reuse_receipt)
    prior_receipt = _validate_local_reuse_receipt(receipt_path, manifest_path)
    inventory_digest = str(prior_receipt["inventory_sha256"])
    rows = read_jsonl(manifest_path, required=True)
    validate_cnn_local_reuse_routes(rows, require_asr=False)
    validate_cnn_local_reuse_inventory_binding(
        rows, inventory, inventory_file_sha256=sha256_file(inventory_path)
    )
    verify_cnn_local_reuse_source_facts(rows)
    model_path = Path(args.model)
    model = WhisperModel(
        str(model_path), device="cpu", compute_type="int8", local_files_only=True,
        cpu_threads=max(1, args.cpu_threads), num_workers=max(1, int(args.workers)),
    )

    input_paths = {
        key: Path(str(value["path"]))
        for key, value in (prior_receipt.get("inputs") or {}).items()
    }

    def checkpoint(current: list[dict[str, Any]]) -> None:
        if sha256_file(inventory_path) != inventory_digest:
            raise RecoveryError("inventory changed during local reuse ASR; reroute before resuming")
        write_jsonl_atomic(manifest_path, current)
        current_receipt = cnn_local_reuse_receipt(
            current, prior_receipt.get("unresolved_axe_packages") or [],
            manifest_path=manifest_path, inventory_path=inventory_path,
            input_paths=input_paths,
        )
        write_json_atomic(receipt_path, current_receipt)

    rows = transcribe_cnn_local_reuse_sources(
        rows, model, model_path=model_path, checkpoint=checkpoint,
        workers=max(1, int(args.workers)),
    )
    if sha256_file(inventory_path) != inventory_digest:
        raise RecoveryError("inventory changed before the local reuse ASR receipt was finalized")
    write_jsonl_atomic(manifest_path, rows)
    receipt = cnn_local_reuse_receipt(
        rows, prior_receipt.get("unresolved_axe_packages") or [],
        manifest_path=manifest_path, inventory_path=inventory_path,
        input_paths=input_paths,
    )
    write_json_atomic(receipt_path, receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


def materialize_cnn_local_reuse_acquisitions(
    route_rows: Sequence[dict[str, Any]],
    inventory_rows: Sequence[dict[str, Any]],
    *,
    inventory_file_sha256: str,
    output_inventory: Path,
    expected_sources: int = 38,
    expected_package_routes: int = 42,
) -> tuple[list[dict[str, Any]], int]:
    """Bind ASR-ready local recordings into the normal acquisition chain.

    The immutable routing inventory remains untouched.  This derived snapshot
    adds one hash-bound acquisition item to each routed package so the existing
    lexical alignment, identity, cutting, and final-receipt code can operate
    without treating show/date/GUID metadata as speaker evidence.
    """
    validate_cnn_local_reuse_routes(
        route_rows, expected_sources=expected_sources,
        expected_package_routes=expected_package_routes, require_asr=True,
    )
    validate_cnn_local_reuse_inventory_binding(
        route_rows, inventory_rows,
        inventory_file_sha256=inventory_file_sha256,
    )
    verify_cnn_local_reuse_source_facts(route_rows)
    materialized = json.loads(json.dumps(list(inventory_rows)))
    by_package = {str(row["package"]): row for row in materialized}
    routed = 0
    for route_row in sorted(route_rows, key=lambda row: str(row["physical_source_id"])):
        source = route_row["source"]
        asr = route_row["asr"]
        asr_receipt = _read_json_object(Path(str(asr["receipt"])))
        provider = "cnn-local-reuse-podcast"
        media_id = str(route_row["physical_source_id"])
        reference_url = str(route_row["reference_url"])
        source_receipt_path = Path(str(asr["receipt"])).parent / "source.json"
        source_receipt = {
            "schema": "speech-source-media-acquisition-v1",
            "status": "acquired",
            "acquired_at": str(asr_receipt.get("generated_at") or utc_now()),
            "provider": provider,
            "media_id": media_id,
            "media_url": reference_url,
            "reference_url": reference_url,
            "wav": str(Path(str(source["wav"])).resolve()),
            "wav_sha256": source["source_audio_sha256"],
            "source_audio_sha256": source["source_audio_sha256"],
            "source_pcm_sha256": source["source_pcm_sha256"],
            "sample_rate": int(source["sample_rate"]),
            "channels": int(source["channels"]),
            "sample_width_bytes": int(source["sample_width_bytes"]),
            "frame_count": int(source["frame_count"]),
            "speaker_identity_authority": False,
            "program_title_date_airtime_identity_authority": False,
        }
        write_json_atomic(source_receipt_path, source_receipt)
        for package_route in route_row["package_routes"]:
            package = str(package_route["package"])
            record = by_package.get(package)
            if record is None:
                raise RecoveryError(f"local reuse route package vanished: {package}")
            caption_rowids = sorted(
                int(rowid)
                for member in record.get("members") or []
                for rowid in member.get("rowids") or []
            )
            item = {
                "schema": "speech-source-media-acquisition-v1",
                "status": "acquired",
                "acquired_at": source_receipt["acquired_at"],
                "provider": provider,
                "media_id": media_id,
                "media_url": reference_url,
                "reference_url": reference_url,
                "transcript_package_url": package,
                "package_id": package_route["package_id"],
                "caption_rowids": caption_rowids,
                "source_offset_sec": 0.0,
                "wav": source_receipt["wav"],
                "wav_sha256": source["source_audio_sha256"],
                "asr_transcript": str(Path(str(asr["transcript"])).resolve()),
                "asr_transcript_receipt": str(Path(str(asr["receipt"])).resolve()),
                "source_receipt": str(source_receipt_path.resolve()),
                "source_private": False,
                "archive_source": {
                    "access": "public-hash-bound-local-reuse",
                    "source_private": False,
                    "collection_provenance": route_row["collection_provenance"],
                },
            }
            record["acquisition"] = {
                "status": "acquired",
                "acquired_at": source_receipt["acquired_at"],
                "items": [item],
            }
            record.pop("alignment", None)
            routed += 1
    if routed != expected_package_routes:
        raise RecoveryError(
            f"local reuse materialization expected {expected_package_routes} routes, got {routed}"
        )
    persist(materialized, output_inventory)
    return materialized, routed


def run_local_reuse_materialize(args: argparse.Namespace) -> int:
    inventory_path = Path(args.inventory)
    inventory, _ = _validated_inventory_input(inventory_path)
    manifest_path = Path(args.local_reuse_manifest)
    receipt_path = Path(args.local_reuse_receipt)
    receipt = _validate_local_reuse_receipt(receipt_path, manifest_path)
    rows = read_jsonl(manifest_path, required=True)
    output_inventory = Path(args.local_reuse_aligned_inventory)
    _, routed = materialize_cnn_local_reuse_acquisitions(
        rows, inventory,
        inventory_file_sha256=str(receipt["inventory_sha256"]),
        output_inventory=output_inventory,
    )
    result = {
        "status": "PASS",
        "schema": "speech-source-media-local-reuse-materialization-v1",
        "source_manifest_sha256": sha256_file(manifest_path),
        "source_receipt_sha256": sha256_file(receipt_path),
        "inventory": str(output_inventory.resolve()),
        "inventory_sha256": sha256_file(output_inventory),
        "package_routes": routed,
        "identity_accepted": False,
        "cleanup_authorized": False,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "inventory", "discover", "acquire", "align", "audit", "status",
            "local-reuse-route", "local-reuse-asr", "local-reuse-materialize",
        ),
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--inventory", default=str(INVENTORY_PATH))
    parser.add_argument("--media-root", default=str(DEFAULT_MEDIA_ROOT))
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--roster", default=str(DEFAULT_ROSTER))
    parser.add_argument(
        "--member-gap",
        default="",
        help=(
            "optional speech-member-profile-gap-v1 JSONL; restrict and "
            "prioritize work to rows containing target_recovery members"
        ),
    )
    parser.add_argument("--candidate-manifest", default=str(DEFAULT_MEDIA_ROOT / "candidate_clips.jsonl"))
    parser.add_argument("--candidate-receipt", default=str(DEFAULT_MEDIA_ROOT / "candidate_clips.receipt.json"))
    parser.add_argument("--supplement-manifest", default=str(SUPPLEMENT_MANIFEST))
    parser.add_argument("--supplement-receipt", default=str(SUPPLEMENT_RECEIPT))
    parser.add_argument("--sotu-targets", default=str(SOTU_TARGETS))
    parser.add_argument("--sotu-fetch-log", default=str(SOTU_FETCH_LOG))
    parser.add_argument("--sotu-root", default=str(SOTU_AUDIO_ROOT))
    parser.add_argument("--axe-manifest", default=str(AXE_MANIFEST))
    parser.add_argument("--axe-fetch-log", default=str(AXE_FETCH_LOG))
    parser.add_argument("--local-reuse-asr-root", default=str(LOCAL_REUSE_ASR_ROOT))
    parser.add_argument("--local-reuse-manifest", default=str(LOCAL_REUSE_MANIFEST))
    parser.add_argument("--local-reuse-receipt", default=str(LOCAL_REUSE_RECEIPT))
    parser.add_argument(
        "--local-reuse-aligned-inventory",
        default=str(LOCAL_REUSE_ALIGNED_INVENTORY),
    )
    parser.add_argument("--source", default="", help="comma-separated source families")
    parser.add_argument("--package-id", default="", help="operate on one exact inventory package ID")
    parser.add_argument("--max-packages", type=int, default=0)
    parser.add_argument("--candidate-limit", type=int, default=4)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--pause", type=float, default=0.35)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--retry-blocked", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--cnn-search", action="store_true")
    parser.add_argument("--minimum-ratio", type=float, default=0.68)
    parser.add_argument("--cpu-threads", type=int, default=2)
    parser.add_argument("--no-verify-audio-hashes", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.action == "inventory":
        return run_inventory(args)
    if args.action == "discover":
        return run_discover(args)
    if args.action == "acquire":
        return run_acquire(args)
    if args.action == "align":
        return run_align(args)
    if args.action == "audit":
        return run_audit(args)
    if args.action == "local-reuse-route":
        return run_local_reuse_route(args)
    if args.action == "local-reuse-asr":
        return run_local_reuse_asr(args)
    if args.action == "local-reuse-materialize":
        return run_local_reuse_materialize(args)
    rows = read_jsonl(Path(args.inventory), required=True)
    print(json.dumps(summarize(rows), ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecoveryError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        raise SystemExit(2)
