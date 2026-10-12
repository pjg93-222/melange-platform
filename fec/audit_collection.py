#!/usr/bin/env python3
"""Strict audit for HouseLive sources/clips, interview turns, and press text."""

from __future__ import annotations

import argparse
from array import array
import codecs
from collections import Counter
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import wave

from collect.press_receipt import ReceiptTimestampError, parse_receipt_timestamp
from paths import ROOT

# Of the inputs a local-youtube verification receipt declares, these are the ones
# that decide WHO a voice belongs to.  A change to any of them voids the
# attestation; see verified_local_youtube_rows for why the rest do not.
IDENTITY_CRITICAL_INPUTS = ("anchor_receipt", "anchor_vectors", "model_manifest")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        choices=("all", "press"),
        default="all",
        help="audit every intake domain or only the independently promotable press run",
    )
    parser.add_argument("--house-root", type=Path, default=Path(r"C:\melange-data\houselive"))
    parser.add_argument(
        "--clip-manifest",
        type=Path,
        default=Path(r"C:\melange-data\member_clips\clips_manifest.jsonl"),
    )
    parser.add_argument("--db", type=Path, default=Path("social_media.db"))
    parser.add_argument("--press", type=Path, default=Path("press_text_cache.jsonl"))
    parser.add_argument(
        "--press-delta-root",
        type=Path,
        default=None,
        help="audited press refresh root; default is the newest valid outputs/press_refresh_* run",
    )
    parser.add_argument("--press-date-floor", default="2026-06-25")
    parser.add_argument("--roster", type=Path, default=Path("data/engine/congestion.json"))
    parser.add_argument("--wj-root", type=Path, default=Path(r"C:\melange-data\wj_audio"))
    parser.add_argument("--axe-root", type=Path, default=Path(r"C:\melange-data\axe_audio"))
    parser.add_argument(
        "--axe-features",
        type=Path,
        default=Path("outputs/axe_pilot_20260827/axe_features.jsonl"),
    )
    parser.add_argument("--youtube-root", type=Path, default=Path(r"C:\melange-data\youtube_audio"))
    parser.add_argument("--audio-pilot", type=Path, default=Path("audio_pilot"))
    parser.add_argument("--output", type=Path, help="atomically write the JSON audit receipt")
    parser.add_argument(
        "--accepted-press-receipts-dir",
        type=Path,
        default=Path("data/engine/press_collection_receipts"),
        help="archive each accepted press-run receipt for cumulative promotion",
    )
    return parser.parse_args(argv)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl_strict(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise RuntimeError("invalid JSON: %s line %d" % (path, number)) from exc
            if not isinstance(row, dict):
                raise RuntimeError("non-object JSON: %s line %d" % (path, number))
            rows.append(row)
    return rows


def verified_local_youtube_rows() -> set[tuple]:
    """Load hash-bound exact cuts from successful write-mode verification runs."""
    verified = set()
    speech_root = Path(ROOT) / "data" / "speech"
    # Every write-mode receipt is read, not just the one named "current".  Preferring
    # "current" whenever it existed made every sibling structurally invisible: the two
    # runs of 2026-08-31 attested 30 and 31 clips, disjoint, and only the 30 could ever
    # be seen.
    receipt_paths = sorted(
        set(speech_root.glob("local_youtube_exact_window_verification*_v1.receipt.json"))
    )
    for receipt_path in receipt_paths:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        if (
            receipt.get("schema") != "local-youtube-exact-window-verification-receipt-v1"
            or receipt.get("status") != "PASS"
            or receipt.get("mode") != "write"
        ):
            continue
        # Declared inputs split into two classes and only one bears on whether an
        # attested row is TRUE.  The identity-critical set decides who a voice belongs
        # to, so any drift there voids the attestation and they are mandatory here --
        # absent counts as failure, never as exemption.  The others (turn candidates,
        # member census) decide only which clips were ATTEMPTED, and sibling producers
        # rewrite them routinely: on 2026-08-31 the candidates file was regenerated
        # three minutes before one receipt sealed and the census six seconds after the
        # other, retroactively voiding 61 true attestations nobody had touched.  Row
        # identity is unaffected by this: each tuple below still has to match on wav
        # path, member, video, both bounds and the wav hash.
        inputs = receipt.get("inputs") or {}
        identity_inputs = [inputs.get(name) for name in IDENTITY_CRITICAL_INPUTS]
        if not all(isinstance(item, dict) for item in identity_inputs):
            continue
        if any(
            not Path(str(item.get("path") or "")).is_file()
            or sha256(Path(str(item.get("path") or ""))) != item.get("sha256")
            for item in identity_inputs
        ):
            continue
        output = Path(str((receipt.get("output") or {}).get("path") or ""))
        if not output.is_file() or sha256(output) != (receipt.get("output") or {}).get("sha256"):
            continue
        for row in read_jsonl_strict(output):
            if row.get("status") != "PASS_EXACT_WINDOW" or row.get("final_identity_acceptance") is not True:
                continue
            verified.add((
                str(Path(str(row.get("out_wav") or "")).resolve()),
                row.get("member"),
                str(row.get("video_id")),
                round(float(row.get("start_sec")), 6),
                round(float(row.get("end_sec")), 6),
                row.get("wav_sha256"),
            ))
    return verified


def wav_facts(path: Path):
    with wave.open(str(path), "rb") as handle:
        return (
            handle.getnchannels(),
            handle.getframerate(),
            handle.getsampwidth(),
            handle.getnframes(),
        )


def pcm16_rms(payload: bytes) -> float:
    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768.0


def wav_facts_and_rms(path: Path):
    total_squares = 0
    sample_count = 0
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        width = handle.getsampwidth()
        frames = handle.getnframes()
        if width != 2:
            return channels, rate, width, frames, None
        while True:
            payload = handle.readframes(1024 * 1024)
            if not payload:
                break
            samples = array("h")
            samples.frombytes(payload)
            if sys.byteorder == "big":
                samples.byteswap()
            total_squares += sum(sample * sample for sample in samples)
            sample_count += len(samples)
    rms = (
        math.sqrt(total_squares / sample_count) / 32768.0
        if sample_count else 0.0
    )
    return channels, rate, width, frames, rms


def duplicate_count(values) -> int:
    return sum(1 for count in Counter(values).values() if count > 1)


def audit_house(root: Path, clip_manifest: Path, roster_path: Path):
    errors = []
    source_rows = read_jsonl_strict(root / "manifest.jsonl")
    source_dates = [row.get("date") for row in source_rows]
    if len(source_rows) != 12 or len(set(source_dates)) != 12:
        errors.append("expected 12 unique House source dates, got %d/%d" % (
            len(source_rows), len(set(source_dates))))
    source_by_date = {row.get("date"): row for row in source_rows}
    source_durations = {}
    source_hours = 0.0
    for row in source_rows:
        date = row.get("date")
        paths = {
            "broadcastevents_sha256": root / "broadcastevents" / (date + ".json"),
            "transcript_sha256": root / "transcripts" / (date + ".json"),
            "vtt_sha256": root / "vtt" / (date + ".vtt"),
            "wav_sha256": Path(row.get("wav_path") or ""),
        }
        for field, path in paths.items():
            if not path.is_file():
                errors.append("missing %s for %s: %s" % (field, date, path))
            elif not row.get(field) or sha256(path) != row[field]:
                errors.append("hash mismatch %s for %s" % (field, date))
        if paths["vtt_sha256"].is_file():
            payload = paths["vtt_sha256"].read_bytes()
            if payload.startswith(codecs.BOM_UTF8):
                payload = payload[len(codecs.BOM_UTF8):]
            if not payload.lstrip().startswith(b"WEBVTT"):
                errors.append("VTT header mismatch for %s" % date)
        if paths["wav_sha256"].is_file():
            channels, rate, width, frames = wav_facts(paths["wav_sha256"])
            if (channels, rate, width) != (1, 16000, 2):
                errors.append("source WAV contract mismatch for %s" % date)
            source_hours += frames / float(rate) / 3600.0
            source_durations[date] = frames / float(rate)

    all_clips = read_jsonl_strict(clip_manifest)
    all_errors = []
    all_paths = [row.get("out_wav") for row in all_clips]
    all_logical = [
        (row.get("outlet"), row.get("sid"), row.get("seg"), row.get("member"))
        for row in all_clips
    ]
    if duplicate_count(all_paths):
        all_errors.append("duplicate global out_wav groups")
    if duplicate_count(all_logical):
        all_errors.append("duplicate global logical clip groups")
    all_clip_hours = 0.0
    clip_audio_facts = {}
    for row in all_clips:
        path = Path(row.get("out_wav") or "")
        if not path.is_file():
            all_errors.append("missing accepted clip: %s" % path)
            continue
        if not row.get("sha256") or sha256(path) != row["sha256"]:
            all_errors.append("accepted clip hash mismatch: %s" % path)
        channels, rate, width, frames, computed_rms = wav_facts_and_rms(path)
        clip_audio_facts[str(path)] = (channels, rate, width, frames, computed_rms)
        if (channels, rate, width) != (1, 16000, 2):
            all_errors.append("accepted clip WAV contract mismatch: %s" % path)
        all_clip_hours += frames / float(rate) / 3600.0
        if int(row.get("frames") or 0) != frames:
            all_errors.append("accepted clip frame-count mismatch: %s" % path)
        stored_rms = float(row.get("rms") or 0.0)
        if computed_rms is None or computed_rms <= 0.0:
            all_errors.append("accepted clip recomputed nonpositive RMS: %s" % path)
        elif stored_rms <= 0.0 or abs(stored_rms - computed_rms) > max(1e-7, computed_rms * 1e-5):
            all_errors.append("accepted clip stored/recomputed RMS mismatch: %s" % path)

    house = [row for row in all_clips if row.get("outlet") == "HouseLive"]
    out_paths = [row.get("out_wav") for row in house]
    logical = [
        (row.get("date"), row.get("seg"), row.get("member"), row.get("text_sha256"))
        for row in house
    ]
    if duplicate_count(out_paths):
        errors.append("duplicate House out_wav groups")
    if duplicate_count(logical):
        errors.append("duplicate House logical turn groups")
    if set(row.get("date") for row in house) != set(source_dates):
        errors.append("House clip/source date sets differ")

    clip_hours = 0.0
    clip_members = set()
    for row in house:
        date = row.get("date")
        path = Path(row.get("out_wav") or "")
        if not path.is_file():
            errors.append("missing clip: %s" % path)
            continue
        if not row.get("sha256") or sha256(path) != row["sha256"]:
            errors.append("clip hash mismatch: %s" % path)
        channels, rate, width, frames, computed_rms = clip_audio_facts[str(path)]
        if (channels, rate, width) != (1, 16000, 2):
            errors.append("clip WAV contract mismatch: %s" % path)
        if computed_rms is None or computed_rms <= 0.0:
            errors.append("recomputed nonpositive clip RMS: %s" % path)
        for field in ("artifact_id", "turn_id", "original_ordinal"):
            if row.get(field) is None or row.get(field) == "":
                errors.append("missing %s: %s" % (field, path))
        source_row = source_by_date.get(date, {})
        if row.get("source_audio_sha256") != source_row.get("wav_sha256"):
            errors.append("source hash join mismatch: %s" % path)
        clip_hours += frames / float(rate) / 3600.0
        clip_members.add(row.get("member"))

    # Reconstruct the cutter's complete eligible-span universe. Every eligible House turn must
    # be represented exactly once by either an accepted manifest row or a typed rejection.
    from speech.houselive_cut import eligible_spans, surname_index

    surnames = surname_index(roster_path.resolve())
    expected = set()
    for source_date in source_dates:
        transcript_path = root / "transcripts" / (source_date + ".json")
        transcript_rows = json.loads(transcript_path.read_text(encoding="utf-8-sig"))
        for ordinal, member, _label, _text, _start, _end in eligible_spans(
            transcript_rows, surnames, source_durations[source_date]
        ):
            expected.add((source_date, int(ordinal), member))

    accepted = {(row.get("date"), int(row.get("seg")), row.get("member")) for row in house}
    rejection_path = clip_manifest.parent / "clips_rejections.jsonl"
    rejection_rows = read_jsonl_strict(rejection_path) if rejection_path.is_file() else []
    house_rejections = [row for row in rejection_rows if row.get("outlet") == "HouseLive"]
    rejected = {
        (row.get("sid"), int(row.get("seg")), row.get("member"))
        for row in house_rejections
    }
    if len(rejected) != len(house_rejections):
        errors.append("duplicate House rejection logical keys")
    if accepted & rejected:
        errors.append("House accepted/rejected turn sets overlap")
    missing_dispositions = expected - accepted - rejected
    extra_dispositions = (accepted | rejected) - expected
    if missing_dispositions:
        errors.append("%d eligible House spans lack a disposition" % len(missing_dispositions))
    if extra_dispositions:
        errors.append("%d House dispositions are not eligible spans" % len(extra_dispositions))
    for row in house_rejections:
        if row.get("reason") != "zero_rms":
            errors.append("unrecognized House rejection reason: %r" % row.get("reason"))
            continue
        source_path = Path(row.get("src_wav") or "")
        if not source_path.is_file():
            errors.append("missing House rejection source: %s" % source_path)
            continue
        with wave.open(str(source_path), "rb") as source:
            rate = source.getframerate()
            first = round(float(row.get("start")) * rate)
            frames = max(1, round((float(row.get("end")) - float(row.get("start"))) * rate))
            source.setpos(first)
            rejected_rms = pcm16_rms(source.readframes(frames))
        if rejected_rms != 0.0:
            errors.append("House zero-RMS rejection recomputed nonzero: %s" % rejected_rms)

    return {
        "source_days": len(source_rows),
        "source_hours": round(source_hours, 3),
        "clip_rows": len(house),
        "clip_hours": round(clip_hours, 3),
        "clip_members": len(clip_members),
        "eligible_spans": len(expected),
        "rejected_spans": len(house_rejections),
        "all_clip_rows": len(all_clips),
        "all_clip_hours": round(all_clip_hours, 3),
        "all_clip_members": len({row.get("member") for row in all_clips if row.get("member")}),
        "all_clip_by_outlet": dict(sorted(Counter(
            row.get("outlet") or "UNKNOWN" for row in all_clips
        ).items())),
        "all_clip_errors": all_errors,
        "errors": errors,
    }


INTERVIEW_HOST_LABELS = {
    "transcripts.cnn.com": "CNN",
    "www.cnn.com": "CNN",
    "cnn.com": "CNN",
    "www.foxnews.com": "Fox News",
    "foxnews.com": "Fox News",
    "www.ms.now": "MSNBC / MS NOW",
    "ms.now": "MSNBC / MS NOW",
    "www.msnbc.com": "MSNBC / MS NOW",
    "msnbc.com": "MSNBC / MS NOW",
    "www.npr.org": "NPR",
    "npr.org": "NPR",
    "www.pbs.org": "PBS",
    "pbs.org": "PBS",
    "www.c-span.org": "C-SPAN",
    "c-span.org": "C-SPAN",
    "www.nbcnews.com": "NBC News / Meet the Press",
    "nbcnews.com": "NBC News / Meet the Press",
    "www.cbsnews.com": "CBS News",
    "cbsnews.com": "CBS News",
    "abcnews.go.com": "ABC News",
    "hughhewitt.com": "Hugh Hewitt Show",
    "www.hughhewitt.com": "Hugh Hewitt Show",
    "www.csis.org": "CSIS",
    "csis.org": "CSIS",
    "www.cfr.org": "CFR",
    "cfr.org": "CFR",
    "www.aspensecurityforum.org": "Aspen Security Forum",
    "aspensecurityforum.org": "Aspen Security Forum",
    "www.aei.org": "AEI",
    "aei.org": "AEI",
}


def interview_source_label(package: str) -> str:
    value = str(package or "")
    if "/washington-journal/" in value.lower():
        return "Washington Journal"
    match = re.match(r"https?://([^/]+)/", value, re.I)
    host = match.group(1).lower() if match else ""
    return INTERVIEW_HOST_LABELS.get(host, host or "Other interview source")


def _source_receipt(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "INVALID"}
    if not isinstance(payload, dict):
        return {"status": "INVALID"}
    return payload


def audit_db(path: Path, source_receipts_dir: Path | None = None):
    errors = []
    source_receipts_dir = source_receipts_dir or (
        Path(ROOT) / "data" / "engine" / "interview_source_receipts"
    )
    connection = sqlite3.connect("file:%s?mode=ro" % path.resolve().as_posix(), uri=True)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(speeches)")}
        base = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT member), COUNT(DISTINCT package), "
            "MIN(date), MAX(date) FROM speeches WHERE source='INTERVIEW'"
        ).fetchone()
        text_field = "text" if "text" in columns else "speech" if "speech" in columns else None
        duplicate_groups = None
        if text_field:
            duplicate_groups = connection.execute(
                "SELECT COUNT(*) FROM (SELECT member,date,package,%s,COUNT(*) n "
                "FROM speeches WHERE source='INTERVIEW' "
                "GROUP BY member,date,package,%s HAVING n>1)" % (text_field, text_field)
            ).fetchone()[0]
        if not base[0]:
            errors.append("interview corpus is empty")
        if duplicate_groups:
            errors.append("%d exact duplicate interview-turn groups" % duplicate_groups)
        source_rows = {}
        for member, observed, package, turns, words in connection.execute(
            "SELECT member,date,package,COUNT(*),COALESCE(SUM(nwords),0) "
            "FROM speeches WHERE source='INTERVIEW' GROUP BY member,date,package"
        ):
            label = interview_source_label(package)
            summary = source_rows.setdefault(label, {
                "turns": 0, "appearances": 0, "unique_packages": set(),
                "members": set(), "min_accepted_date": None, "max_accepted_date": None,
                "words": 0,
            })
            summary["turns"] += int(turns or 0)
            summary["appearances"] += 1
            summary["words"] += int(words or 0)
            summary["unique_packages"].add(package)
            summary["members"].add(member)
            if observed:
                summary["min_accepted_date"] = min(
                    filter(None, (summary["min_accepted_date"], observed)))
                summary["max_accepted_date"] = max(
                    filter(None, (summary["max_accepted_date"], observed)))

        # A collector horizon says which dates were checked; an accepted-content
        # date says when a roster member actually appeared. They are deliberately
        # separate: a member-free day is not missing collection, and an ended
        # archive (Fox/MS NOW) can be checked today while its content stays old.
        yesterday = date.today() - timedelta(days=1)
        horizon_by_source = {}
        try:
            for key, status in connection.execute(
                "SELECT key,status FROM iv_fetch_log WHERE status IN ('ok','noep','empty')"
            ):
                match = re.search(r"(\d{4}-\d{2}-\d{2})$", str(key or ""))
                if not match:
                    continue
                label = (
                    "CNN" if str(key).startswith("cnn:") else
                    "PBS" if str(key).startswith("pbs:") else
                    "NPR" if str(key).startswith("npr:") else None
                )
                if label:
                    horizon_by_source[label] = max(
                        horizon_by_source.get(label, ""), match.group(1))
        except sqlite3.Error:
            horizon_by_source = {}

        receipt_specs = {
            "Fox News": ("fox.json", "historical_archive"),
            "MSNBC / MS NOW": ("msnow.json", "historical_archive"),
            "Washington Journal": ("washington_journal.json", "bounded_incremental"),
        }
        source_receipts = {}
        for label, (filename, mode) in receipt_specs.items():
            receipt = _source_receipt(source_receipts_dir / filename)
            source_receipts[label] = receipt
            horizon = receipt.get("collector_horizon")
            if horizon:
                horizon_by_source[label] = str(horizon)
            if label in source_rows:
                source_rows[label]["freshness_mode"] = receipt.get("freshness_mode") or mode
                source_rows[label]["collector_status"] = receipt.get("status") or "NOT_RUN"
                source_rows[label]["collector_checked_at"] = receipt.get("completed_at")
                source_rows[label]["source_terminal_date"] = (
                    receipt.get("source_terminal_date")
                    or receipt.get("source_latest_program_date")
                )
            if receipt and receipt.get("status") not in ("COMPLETE",):
                errors.append("%s collector receipt is %s" % (label, receipt.get("status")))

        for label, summary in source_rows.items():
            summary.setdefault("freshness_mode", "scheduled_source")
            summary.setdefault("collector_status", "LOG_ONLY" if label in horizon_by_source else "UNATTESTED")
            summary.setdefault("collector_checked_at", None)
            summary.setdefault("source_terminal_date", None)
            summary["collector_horizon"] = horizon_by_source.get(label)
            if summary["freshness_mode"] == "historical_archive":
                checked = str(summary.get("collector_checked_at") or "")[:10]
                summary["collector_checked_through_yesterday"] = bool(
                    checked and date.fromisoformat(checked) >= yesterday
                )
            else:
                checked = summary.get("collector_horizon")
                summary["collector_checked_through_yesterday"] = bool(
                    checked and date.fromisoformat(checked) >= yesterday
                )
            summary["unique_packages"] = len(summary["unique_packages"])
            summary["members"] = len(summary["members"])

        horizons = [date.fromisoformat(value) for value in horizon_by_source.values() if value]
        horizon = max(horizons) if horizons else None
        current_window_sources = ("CNN", "PBS", "NPR", "Washington Journal")
        current_windows_complete = all(
            horizon_by_source.get(label)
            and date.fromisoformat(horizon_by_source[label]) >= yesterday
            for label in current_window_sources
        )
        max_observed = date.fromisoformat(base[4]) if base[4] else None
        return {
            "turns": base[0],
            "members": base[1],
            "packages": base[2],
            "min_date": base[3],
            "max_observed_date": base[4],
            "collector_horizon": horizon.isoformat() if horizon else None,
            "collection_checked_through_yesterday": current_windows_complete,
            "fresh_row_observed_through_yesterday": bool(
                max_observed is not None and max_observed >= yesterday
            ),
            "coverage_verification": "per_source_collector_horizon_and_accepted_content_dates",
            "source_coverage": dict(sorted(source_rows.items())),
            "current_day_retryable": bool(
                not current_windows_complete
            ),
            "exact_duplicate_groups": duplicate_groups,
            "errors": errors,
        }
    finally:
        connection.close()


def path_is_within(value: str, root: Path) -> bool:
    try:
        Path(value).resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


AXE_PACKAGE_PREFIX = "https://www.cnn.com/audio/podcasts/axe-files/episodes/"
AXE_FILLERS = {"uh", "um", "erm"}


def canonical_json_sha256(row: dict, omit=()) -> str:
    core = {key: value for key, value in row.items() if key not in omit}
    payload = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def axe_content_words(text: str) -> int:
    tokens = re.findall(r"[a-z0-9']+", (text or "").lower())
    return sum(token not in AXE_FILLERS for token in tokens)


def unique_axe_feature_turn_mapping(feature_rows, turn_rows, sid):
    from functools import lru_cache

    counts = []
    previous_seg = -1
    for row in feature_rows:
        seg = row.get("seg")
        words = row.get("words")
        if isinstance(seg, bool) or not isinstance(seg, int) or seg <= previous_seg:
            raise RuntimeError("Axe feature segments not strictly increasing: %s" % sid)
        if isinstance(words, bool) or not isinstance(words, int) or words < 1:
            raise RuntimeError("Axe feature word count invalid: %s seg=%r" % (sid, seg))
        previous_seg = seg
        counts.append(words)

    turn_counts = [axe_content_words(row[4]) for row in turn_rows]

    @lru_cache(maxsize=None)
    def ways(feature_index, floor):
        if feature_index == len(counts):
            return 1
        total = 0
        remaining = len(counts) - feature_index - 1
        for turn_index in range(floor, len(turn_rows) - remaining):
            if turn_counts[turn_index] != counts[feature_index]:
                continue
            total += ways(feature_index + 1, turn_index + 1)
            if total > 1:
                return 2
        return total

    if ways(0, 0) != 1:
        raise RuntimeError("Axe feature-to-turn mapping is not unique: %s" % sid)
    mapping = {}
    floor = 0
    for feature_index, feature in enumerate(feature_rows):
        remaining = len(counts) - feature_index - 1
        choices = [
            turn_index
            for turn_index in range(floor, len(turn_rows) - remaining)
            if turn_counts[turn_index] == counts[feature_index]
            and ways(feature_index + 1, turn_index + 1) > 0
        ]
        if len(choices) != 1:
            raise RuntimeError("Axe mapping reconstruction diverged: %s" % sid)
        turn = turn_rows[choices[0]]
        mapping[int(feature["seg"])] = (feature, turn)
        floor = choices[0] + 1
    return mapping


def audit_audio_supply(
    clip_manifest: Path,
    wj_root: Path,
    axe_root: Path,
    axe_features: Path,
    db_path: Path,
    youtube_root: Path,
    audio_pilot: Path,
):
    errors = []
    accepted = read_jsonl_strict(clip_manifest)
    rejection_path = clip_manifest.parent / "clips_rejections.jsonl"
    rejections = read_jsonl_strict(rejection_path) if rejection_path.is_file() else []

    # Washington Journal: deduplicate v1/v2 just as the importer does, then require an explicit
    # accepted or rejected disposition for every unique candidate.
    selected_wj = {}
    for version, filename in (("v1", "turn_manifest.jsonl"), ("v2", "turn_manifest_v2.jsonl")):
        for row in read_jsonl_strict(wj_root / filename):
            start = float(row.get("member_start", row.get("start")))
            full_key = (str(row.get("pid")), row.get("member"), start, float(row.get("end")))
            selected_wj.setdefault(full_key, (version, row))
    candidate_keys = set(selected_wj)
    candidate_base = [(key[0], key[1], round(key[2])) for key in candidate_keys]
    if duplicate_count(candidate_base):
        errors.append("Washington Journal candidates collide on program/member/start")
    wj_accepted_rows = [
        row for row in accepted
        if row.get("outlet") == "CSPAN"
        and (
            path_is_within(row.get("src_wav") or "", wj_root)
            or path_is_within(row.get("out_wav") or "", wj_root)
        )
    ]
    # v2/gap files can begin with context padding.  Accepted rows are bound to
    # the recovered official member start, so match them back to candidates by
    # the immutable official turn end rather than the raw padded file start.
    wj_accepted_by_end = {
        (str(row.get("sid")), row.get("member"), float(row.get("transcript_turn_end")))
        for row in wj_accepted_rows
        if row.get("transcript_turn_end") is not None
    }
    wj_accepted_by_start = {
        (str(row.get("sid")), row.get("member"), round(float(row.get("start"))))
        for row in wj_accepted_rows
        if row.get("start") is not None
    }
    wj_rejection_rows = [row for row in rejections if row.get("outlet") == "CSPAN"]
    wj_rejected_all = {
        (str(row.get("sid")), row.get("member"), round(float(row.get("seg"))))
        for row in wj_rejection_rows
    }
    # Rejection history is append-only. A later verified acceptance supersedes
    # an earlier refusal for the same logical candidate; duplicate attempts are
    # retained as history, not counted as multiple terminal dispositions.
    wj_accepted = {
        key for key in candidate_keys
        if (
            (key[0], key[1], key[3]) in wj_accepted_by_end
            or (key[0], key[1], round(key[2])) in wj_accepted_by_start
        )
    }
    wj_rejected = {
        key for key in candidate_keys - wj_accepted
        if (key[0], key[1], round(key[2])) in wj_rejected_all
    }
    if wj_accepted | wj_rejected != candidate_keys:
        errors.append("Washington Journal dispositions do not equal unique candidates")

    # Axe: independently replay the exact GUID/package/member/date binding, the unique
    # monotonic feature-to-database-turn join, and all stored hashes.  Full episodes without
    # such a binding remain source audio and are never promoted merely because a clip exists.
    axe_manifest = read_jsonl_strict(axe_root / "axe_manifest.jsonl")
    axe_sources = {str(Path(row.get("wav") or "").resolve()) for row in axe_manifest}
    axe_by_guid = {}
    for row in axe_manifest:
        guid = row.get("guid")
        if not isinstance(guid, str) or not guid:
            errors.append("Axe manifest row lacks a GUID")
        elif guid in axe_by_guid:
            errors.append("duplicate Axe manifest GUID: %s" % guid)
        else:
            axe_by_guid[guid] = row
    feature_rows = read_jsonl_strict(axe_features)
    features_by_sid = {}
    for row in feature_rows:
        features_by_sid.setdefault(row.get("sid"), []).append(row)
    for rows in features_by_sid.values():
        rows.sort(key=lambda row: row.get("seg", -1))
    axe_accepted_rows = [
        row for row in accepted
        if row.get("show") == "CNN The Axe Files" or path_is_within(row.get("src_wav") or "", axe_root)
    ]
    axe_accepted_sources = {str(Path(row.get("src_wav") or "").resolve()) for row in axe_accepted_rows}
    if not axe_accepted_sources.issubset(axe_sources):
        errors.append("accepted Axe clips reference a source outside axe_manifest")
    verified_axe_sources = set()
    verified_rows = 0
    source_hashes = {}
    uri = "file:" + str(db_path.resolve()).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        replay_by_sid = {}
        for row in axe_accepted_rows:
            sid = row.get("sid")
            seg = row.get("seg")
            guid = row.get("episode_guid")
            if not isinstance(sid, str) or isinstance(seg, bool) or not isinstance(seg, int):
                errors.append("accepted Axe row lacks valid sid/seg")
                continue
            manifest = axe_by_guid.get(guid)
            if manifest is None:
                errors.append("accepted Axe row lacks exact manifest GUID: %s/%s" % (sid, seg))
                continue
            source_path = Path(str(manifest.get("wav") or "")).resolve()
            if source_path != Path(str(row.get("src_wav") or "")).resolve():
                errors.append("Axe source path/GUID mismatch: %s/%s" % (sid, seg))
                continue
            package = AXE_PACKAGE_PREFIX + str(guid)
            if row.get("package") != package or row.get("member") != manifest.get("member"):
                errors.append("Axe package/member binding mismatch: %s/%s" % (sid, seg))
                continue
            if sid not in replay_by_sid:
                turns = connection.execute(
                    "SELECT rowid, member, date, package, text FROM speeches "
                    "WHERE source='INTERVIEW' AND chamber='axefiles' AND package=? ORDER BY rowid",
                    (package,),
                ).fetchall()
                if not turns:
                    errors.append("Axe database package has no turns: %s" % package)
                    replay_by_sid[sid] = {}
                elif any(
                    turn[1] != manifest.get("member") or turn[3] != package or not turn[4]
                    for turn in turns
                ):
                    errors.append("Axe database package/member/text assertion failed: %s" % package)
                    replay_by_sid[sid] = {}
                else:
                    try:
                        replay_by_sid[sid] = unique_axe_feature_turn_mapping(
                            features_by_sid.get(sid, []), turns, sid
                        )
                    except RuntimeError as exc:
                        errors.append(str(exc))
                        replay_by_sid[sid] = {}
            replay = replay_by_sid[sid].get(seg)
            if replay is None:
                errors.append("Axe accepted clip lacks replayed feature/turn: %s/%s" % (sid, seg))
                continue
            feature, turn = replay
            rowid, turn_member, turn_date, turn_package, turn_text = turn
            expected = {
                "provenance_schema": "axe-clip-turn-v1",
                "artifact_id": "axe:%s:speeches:%s:seg:%s" % (guid, rowid, seg),
                "turn_id": "sqlite:speeches:%s" % rowid,
                "package": turn_package,
                "turn_date": turn_date,
                "transcript_path": str(db_path.resolve()) + "#speeches.rowid=%s" % rowid,
                "text_sha256": hashlib.sha256(turn_text.encode("utf-8")).hexdigest(),
                "source_manifest_sha256": canonical_json_sha256(manifest),
                "feature_sha256": canonical_json_sha256(feature, omit=("feature_sha256",)),
            }
            if any(row.get(key) != value for key, value in expected.items()):
                errors.append("Axe stored turn provenance mismatch: %s/%s" % (sid, seg))
                continue
            if (row.get("match"), row.get("words")) != (
                feature.get("match"), feature.get("words")
            ):
                errors.append("Axe clip/feature score or word-count mismatch: %s/%s" % (sid, seg))
                continue
            try:
                source_channels, source_rate, source_width, source_frames = wav_facts(source_path)
                stored_rate = int(row.get("samplerate"))
                expected_start_frame = max(
                    0, min(int(float(feature.get("start")) * source_rate), source_frames)
                )
                expected_end_frame = max(
                    0, min(int(float(feature.get("end")) * source_rate), source_frames)
                )
                stored_start_frame = round(float(row.get("start")) * stored_rate)
                stored_end_frame = round(float(row.get("end")) * stored_rate)
                frame_binding_ok = (
                    source_channels >= 1
                    and source_width >= 1
                    and stored_rate == source_rate
                    and stored_start_frame == expected_start_frame
                    and stored_end_frame == expected_end_frame
                    and int(row.get("frames")) == expected_end_frame - expected_start_frame
                )
            except (TypeError, ValueError, OSError, wave.Error):
                frame_binding_ok = False
            if not frame_binding_ok:
                errors.append("Axe clip/feature PCM-frame mismatch: %s/%s" % (sid, seg))
                continue
            source_key = str(source_path)
            if source_key not in source_hashes:
                source_hashes[source_key] = sha256(source_path)
            if row.get("source_audio_sha256") != source_hashes[source_key]:
                errors.append("Axe source audio hash mismatch: %s/%s" % (sid, seg))
                continue
            if row.get("member") != turn_member:
                errors.append("Axe clip/database member mismatch: %s/%s" % (sid, seg))
                continue
            verified_rows += 1
            verified_axe_sources.add(source_key)
    finally:
        connection.close()

    youtube_manifest = read_jsonl_strict(youtube_root / "youtube_manifest.jsonl")
    youtube_identity = Counter(row.get("identity") or "missing" for row in youtube_manifest)
    youtube_accepted = [
        row for row in accepted
        if path_is_within(row.get("src_wav") or "", youtube_root)
        or path_is_within(row.get("out_wav") or "", youtube_root)
    ]
    verified_youtube = verified_local_youtube_rows()
    youtube_verified = [
        row for row in youtube_accepted
        if (
            str(Path(str(row.get("out_wav") or "")).resolve()),
            row.get("member"),
            str(row.get("sid")),
            round(float(row.get("start")), 6),
            round(float(row.get("end")), 6),
            row.get("sha256"),
        ) in verified_youtube
    ]
    youtube_unverified = [row for row in youtube_accepted if row not in youtube_verified]
    if youtube_unverified:
        errors.append("title-match-unverified YouTube audio entered the accepted manifest")

    pilot_root = audio_pilot.resolve()
    pilot_wavs = list(pilot_root.rglob("*.wav")) if pilot_root.is_dir() else []
    pilot_parts = list(pilot_root.rglob("*.part")) if pilot_root.is_dir() else []
    pilot_accepted = [
        row for row in accepted
        if path_is_within(row.get("src_wav") or "", pilot_root)
        or path_is_within(row.get("out_wav") or "", pilot_root)
    ]
    if pilot_accepted:
        errors.append("unmanifested legacy audio_pilot files entered the accepted manifest")

    return {
        "washington_journal": {
            "v1_rows": len(read_jsonl_strict(wj_root / "turn_manifest.jsonl")),
            "v2_rows": len(read_jsonl_strict(wj_root / "turn_manifest_v2.jsonl")),
            "unique_candidates": len(candidate_keys),
            "accepted": len(wj_accepted),
            "refused": len(wj_rejected),
            "historical_rejection_rows": len(wj_rejection_rows),
            "historical_unique_rejection_keys": len(wj_rejected_all),
            "accepted_members": len({row.get("member") for row in wj_accepted_rows}),
            "context_trimmed": sum(
                row.get("legacy_manifest_version") == "v2"
                and row.get("src_wav") != row.get("out_wav")
                for row in wj_accepted_rows
            ),
            "refusal_reasons": dict(sorted(Counter(
                row.get("reason") or "missing" for row in wj_rejection_rows
            ).items())),
        },
        "axe_files": {
            "source_episodes": len(axe_manifest),
            "transcript_bound_source_episodes": len(verified_axe_sources),
            "unbound_source_episodes": len(axe_sources - verified_axe_sources),
            "accepted_clips": len(axe_accepted_rows),
            "verified_clip_turn_joins": verified_rows,
        },
        "youtube_audio": {
            "source_files": len(youtube_manifest),
            "identity_statuses": dict(sorted(youtube_identity.items())),
            "accepted_clips": len(youtube_accepted),
            "verified_exact_clips": len(youtube_verified),
            "unverified_accepted_clips": len(youtube_unverified),
            "classification": "verified-exact-or-source-only-title-match-unverified",
        },
        "legacy_audio_pilot": {
            "wav_files": len(pilot_wavs),
            "incomplete_part_files": len(pilot_parts),
            "accepted_clips": len(pilot_accepted),
            "classification": "source_only_no_provenance_manifest_or_speaker_binding",
        },
        "errors": errors,
    }


def audit_press(path: Path):
    errors = []
    rows = read_jsonl_strict(path)
    latest = {}
    for row in rows:
        url = row.get("url")
        if url:
            latest[url] = row
    usable = sum(1 for row in latest.values() if len(str(row.get("text") or "").split()) >= 20)
    terminal = sum(
        1 for row in latest.values()
        if row.get("classification") == "out_of_scope_nonarticle_video"
    )
    unresolved = len(latest) - usable - terminal
    if unresolved:
        errors.append("%d production press URLs remain unresolved" % unresolved)
    return {
        "physical_rows": len(rows),
        "unique_urls": len(latest),
        "usable_latest": usable,
        "terminal_nonarticle_videos": terminal,
        "unresolved_latest": unresolved,
        "errors": errors,
    }


def read_csv_strict(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def canonical_url(value: str) -> str:
    value = (value or "").strip().split("#", 1)[0].rstrip("/").lower()
    for prefix in ("https://www.", "http://www.", "https://", "http://"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def body_word_count(row: dict) -> int:
    return len(str(row.get("body") or "").split())


def press_turn_key(row: dict) -> str:
    return "%s#%s" % (
        canonical_url(row.get("url", "")),
        str(row.get("bioguide") or "").strip().upper(),
    )


def audit_press_delta(root: Path, floor_text: str, *, require_complete=False):
    errors = []
    names = (
        "fulltext",
        "articles",
        "host_articles",
        "article_mentions",
        "failed_bodies",
        "dated",
        "undated",
        "out_of_range",
        "scored",
    )
    frames = {}
    for name in names:
        path = root / ("press_delta_%s.csv" % name)
        if not path.is_file():
            errors.append("missing press delta artifact: %s" % path)
            frames[name] = []
        else:
            frames[name] = read_csv_strict(path)

    terminal_path = root / "press_delta_terminal_nonarticles.csv"
    terminal_nonarticles = (
        read_csv_strict(terminal_path) if terminal_path.is_file() else []
    )
    if any(not str(row.get("terminal_reason") or "").strip()
           for row in terminal_nonarticles):
        errors.append("terminal non-article artifact contains an unclassified row")

    urls = {
        name: [canonical_url(row.get("url", "")) for row in rows]
        for name, rows in frames.items()
    }
    turns = {
        name: [press_turn_key(row) for row in rows]
        for name, rows in frames.items()
    }
    for name, values in urls.items():
        if any(not value for value in values):
            errors.append("empty canonical URL in %s" % name)
        if duplicate_count(turns[name]):
            errors.append("duplicate article/member turn groups in %s" % name)

    receipts = set(turns["fulltext"])
    articles = set(turns["articles"])
    failures = set(turns["failed_bodies"])
    terminal_turns = {press_turn_key(row) for row in terminal_nonarticles}
    if receipts & terminal_turns:
        errors.append("terminal non-article rows overlap full-text receipts")
    if articles & failures or articles | failures != receipts:
        errors.append("article/failed-body partition does not equal receipt set")
    if any(body_word_count(row) < 20 for row in frames["articles"]):
        errors.append("articles contains a body under 20 words")
    if any(body_word_count(row) >= 20 for row in frames["failed_bodies"]):
        errors.append("failed_bodies contains a usable article body")
    expected_mentions = {
        press_turn_key(row) for row in frames["articles"]
        if row.get("source") == "OFFICIAL_MENTION"
    }
    if set(turns["article_mentions"]) != expected_mentions:
        errors.append("article_mentions is not the OFFICIAL_MENTION subset")
    if set(turns["host_articles"]) | expected_mentions != articles or (
        set(turns["host_articles"]) & expected_mentions
    ):
        errors.append("host/mention article partitions do not equal usable article rows")
    if any(row.get("source") == "OFFICIAL_MENTION" for row in frames["scored"]):
        errors.append("mention relationships leaked into speaker/stance scoring")

    retry_receipt_path = root / "press_network_retry_receipt.json"
    retry_receipt_valid = False
    retry_receipt = {}
    completion_proof = {}
    if not retry_receipt_path.is_file():
        errors.append("missing live-network press retry receipt")
    else:
        try:
            retry_receipt = json.loads(retry_receipt_path.read_text(encoding="utf-8"))
            parse_receipt_timestamp(retry_receipt.get("completed_at"))
            from collect.build_press_engine_feed import ReceiptRejected, validate_body_completion
            completion_valid = True
            try:
                # The proof says HOW the backlog was cleared: empty when the walk
                # completed every body, tombstone counts when deferred rows were
                # excused.  The receipt must name that, or the relaxation is silent.
                completion_proof = validate_body_completion(
                    root, retry_receipt, require_complete=require_complete) or {}
            except ReceiptRejected as exc:
                completion_valid = False
                errors.append("press completion proof refused [%s]: %s" % (exc.reason, exc.detail))
            retry_receipt_valid = (
                completion_valid and retry_receipt.get("mode") == "live_network_retry"
                and retry_receipt.get("fulltext_rds_sha256")
                    == sha256(root / "press_delta_fulltext.rds")
                and retry_receipt.get("fulltext_csv_sha256")
                    == sha256(root / "press_delta_fulltext.csv")
                and int(retry_receipt.get("fulltext_receipts", -1)) == len(frames["fulltext"])
                and int(retry_receipt.get("usable_article_member_rows", -1))
                    == len(frames["articles"])
                and int(retry_receipt.get("unique_host_articles", -1))
                    == len(set(urls["host_articles"]))
                and int(retry_receipt.get("mention_relationships", -1))
                    == len(frames["article_mentions"])
                and int(retry_receipt.get("failed_bodies", -1)) == len(frames["failed_bodies"])
                and int(retry_receipt.get("exhausted_network_body_failures", -1))
                    == sum(
                        str(row.get("collection_status") or "").endswith("after_network_retry")
                        for row in frames["failed_bodies"]
                    )
                and int(retry_receipt.get("rejected_short_mention_slices", -1))
                    == sum(
                        row.get("collection_status") == "mention_slice_under_20_words"
                        for row in frames["failed_bodies"]
                    )
                and (
                    "terminal_nonarticle_urls" not in retry_receipt
                    or int(retry_receipt.get("terminal_nonarticle_urls", -1))
                    == len(terminal_nonarticles)
                )
            )
        except (OSError, ValueError, TypeError, ReceiptTimestampError, json.JSONDecodeError):
            retry_receipt_valid = False
        if not retry_receipt_valid:
            errors.append("live-network press retry receipt is invalid or mismatched")
    exhausted_network_failures = [
        row for row in frames["failed_bodies"]
        if str(row.get("collection_status") or "").endswith("after_network_retry")
    ]
    rejected_short_mentions = [
        row for row in frames["failed_bodies"]
        if row.get("collection_status") == "mention_slice_under_20_words"
    ]
    # Paul 2026-09-19: a pending row the deferral ledger has tombstoned loses its
    # veto over intake.  The walk also writes it here as body_pending_network_attempt,
    # so it takes the same excuse from the same ledger and function, or this gate
    # refuses a run the completion gate accepted.
    excused_pending = []
    pending_failures = [
        row for row in frames["failed_bodies"]
        if row.get("collection_status") == "body_pending_network_attempt"
    ]
    if pending_failures:
        from collect.press_pending_tombstones import (
            PendingArtifactError, excused_by, read_pending, record_key,
            tombstoned_keys, tombstoned_members, update,
        )
        try:
            ledger = update(root.parent)
            sealed = {record_key(row.get("bioguide"), row.get("url"))
                      for row in read_pending(root)}
        except (PendingArtifactError, OSError):
            ledger = None
        if ledger is not None:
            keys, members = tombstoned_keys(ledger), tombstoned_members(ledger)
            excused_pending = [
                row for row in pending_failures
                if record_key(row.get("bioguide"), row.get("url")) in sealed
                and excused_by(ledger, row.get("bioguide"), row.get("url"), keys, members)
            ]
    unresolved_failures = [
        row for row in frames["failed_bodies"]
        if row not in exhausted_network_failures and row not in rejected_short_mentions
        and row not in excused_pending
    ]
    if unresolved_failures:
        errors.append(
            "%d failed press-delta bodies lack an exhausted network-retry receipt"
            % len(unresolved_failures)
        )

    dated = set(turns["dated"])
    undated = set(turns["undated"])
    out_of_range = set(turns["out_of_range"])
    if dated & undated or dated & out_of_range or undated & out_of_range:
        errors.append("dated/undated/out-of-range partitions overlap")
    if dated | undated | out_of_range != articles:
        errors.append("date partitions do not equal article set")

    floor = date.fromisoformat(floor_text)
    ceiling = date.today()
    for row in frames["dated"]:
        try:
            value = date.fromisoformat(row.get("date_final", ""))
        except ValueError:
            errors.append("invalid dated date: %r" % row.get("date_final"))
            continue
        if not floor <= value <= ceiling:
            errors.append("dated article outside audited window: %s" % value)
    if any(row.get("date_final", "").strip() for row in frames["undated"]):
        errors.append("undated artifact contains a populated date")
    if not set(turns["scored"]).issubset(dated):
        errors.append("scored URLs are not a subset of dated articles")

    return {
        "receipts": len(frames["fulltext"]),
        "articles": len(frames["articles"]),
        "unique_host_articles": len(set(urls["host_articles"])),
        "official_mention_relationships": len(frames["article_mentions"]),
        "failed_bodies": len(frames["failed_bodies"]),
        "exhausted_network_body_failures": len(exhausted_network_failures),
        "rejected_short_mention_slices": len(rejected_short_mentions),
        "terminal_nonarticle_urls": len(terminal_nonarticles),
        "recovered_bodies": int(retry_receipt.get("recovered_bodies", 0) or 0),
        "live_network_retry_receipt_valid": retry_receipt_valid,
        "dated": len(frames["dated"]),
        "undated": len(frames["undated"]),
        "out_of_range": len(frames["out_of_range"]),
        "pending_body_urls": retry_receipt.get("pending_body_urls"),
        "pending_body_urls_tombstoned": completion_proof.get("pending_body_urls_tombstoned", 0),
        # Named separately so a member the walk cannot drain at all reads as a
        # standing coverage hole in the receipt instead of as a clean run.
        "pending_body_urls_excused_by_url": completion_proof.get("excused_by_url", 0),
        "pending_body_urls_excused_by_member": completion_proof.get("excused_by_member", 0),
        "pending_tombstoned_members": completion_proof.get("tombstoned_members", []),
        "pending_failed_bodies_excused": len(excused_pending),
        "cached_date_rejoins": retry_receipt.get("cached_date_rejoins"),
        "revisit_due_urls": retry_receipt.get("revisit_due_urls"),
        "revisit_attempted_urls": retry_receipt.get("revisit_attempted_urls"),
        "revisit_recovered_urls": retry_receipt.get("revisit_recovered_urls"),
        "scored": len(frames["scored"]),
        "errors": errors,
    }


def _receipt_binding(document):
    inputs = document.get("inputs") or {}
    return {
        "press_delta_root": inputs.get("press_delta_root"),
        "press_retry_receipt_sha256": inputs.get("press_retry_receipt_sha256"),
        "press_source_sha256": inputs.get("press_source_sha256"),
    }


def archive_accepted_press_receipt(document, receipt_dir):
    """Persist one immutable accepted-run receipt for cumulative feed rebuilds."""
    if (document.get("schema") != "collection-intake-receipt-v1"
            or document.get("status") != "ACCEPTED"):
        return None
    try:
        run_name = Path(document["inputs"]["press_delta_root"]).name
    except (KeyError, TypeError) as exc:
        raise RuntimeError("accepted collection receipt lacks press_delta_root") from exc
    if not re.fullmatch(r"press_refresh_\d{8}", run_name):
        raise RuntimeError("unsafe press run name in accepted receipt: %r" % run_name)
    receipt_dir = Path(receipt_dir)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    destination = receipt_dir / (run_name + ".json")
    if destination.is_file():
        existing = json.loads(destination.read_text(encoding="utf-8"))
        if _receipt_binding(existing) != _receipt_binding(document):
            raise RuntimeError("accepted press receipt binding changed for %s" % run_name)
        return destination
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    temporary.replace(destination)
    return destination


def main(argv=None) -> int:
    args = parse_args(argv)
    for name in ("db", "press", "roster", "axe_features", "audio_pilot"):
        value = getattr(args, name)
        if value is not None and not value.is_absolute():
            setattr(args, name, Path(ROOT) / value)
    if args.press_delta_root is None:
        from collect.build_press_engine_feed import discover_run
        args.press_delta_root, _retry_receipt = discover_run(Path(ROOT) / "outputs")
    if args.output is not None and not args.output.is_absolute():
        args.output = Path(ROOT) / args.output
    if not args.accepted_press_receipts_dir.is_absolute():
        args.accepted_press_receipts_dir = Path(ROOT) / args.accepted_press_receipts_dir
    press_source_names = (
        "press_delta_scored.csv", "press_delta_host_articles.csv",
        "press_delta_article_mentions.csv", "press_network_retry_receipt.json",
    )
    # New receipts bind the explicit empty pending set. Include it in the
    # before/after source guard without rewriting historical receipt contracts.
    for name in ("press_delta_pending.csv", "press_delta_cached_dates.csv",
                 "press_body_attempt_ledger.rds", "press_official_coverage.json",
                 "press_historical_maintenance.csv"):
        if (args.press_delta_root / name).is_file():
            press_source_names += (name,)
    retry_receipt_name = "press_network_retry_receipt.json"
    missing_press_sources = [
        name for name in press_source_names
        if not (args.press_delta_root / name).is_file()
    ]
    if missing_press_sources:
        # A dated run directory can be caught mid-collection (discovery files only,
        # no press-delta artifacts yet).  Refuse it with a named reason instead of
        # letting the sha256() calls below raise an uncaught FileNotFoundError.
        present_sources = [
            name for name in press_source_names if name not in missing_press_sources
        ]
        inputs = {"press_delta_root": str(args.press_delta_root.resolve())}
        if retry_receipt_name in present_sources:
            inputs["press_retry_receipt_sha256"] = sha256(
                args.press_delta_root / retry_receipt_name)
        inputs["press_source_sha256"] = {
            name: sha256(args.press_delta_root / name)
            for name in present_sources if name != retry_receipt_name
        }
        result = {
            "schema": "collection-intake-receipt-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": ["press"] if args.scope == "press" else [
                "house", "audio_supply", "interviews", "press", "press_delta"
            ],
            "inputs": inputs,
            "status": "REJECTED",
            "errors": [
                "PRESS_SOURCE_MISSING: %s" % name for name in missing_press_sources
            ],
        }
        result["error_count"] = len(result["errors"])
        payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
        print(payload, end="")
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(str(args.output) + ".tmp")
            temporary.write_text(payload, encoding="utf-8", newline="\n")
            temporary.replace(args.output)
        return 2
    press_hashes_before = {
        name: sha256(args.press_delta_root / name) for name in press_source_names
    }
    press_delta_result = audit_press_delta(
        args.press_delta_root, args.press_date_floor, require_complete=args.scope == "press")
    press_hashes_after = {
        name: sha256(args.press_delta_root / name) for name in press_source_names
    }
    if press_hashes_before != press_hashes_after:
        press_delta_result["errors"].append(
            "press delta sources changed while the collection audit was running"
        )
    press_inputs = {
        "press_delta_root": str(args.press_delta_root.resolve()),
        "press_retry_receipt_sha256": press_hashes_after[
            "press_network_retry_receipt.json"
        ],
        "press_source_sha256": {
            name: press_hashes_after[name]
            for name in press_source_names
            # Pending coverage is already bound through the retry receipt;
            # preserve the published three-file feed-source map contract.
            if name not in ("press_network_retry_receipt.json", "press_delta_pending.csv",
                            "press_delta_cached_dates.csv", "press_body_attempt_ledger.rds",
                            "press_official_coverage.json", "press_historical_maintenance.csv")
        },
    }
    result = {
        "schema": "collection-intake-receipt-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": ["press"] if args.scope == "press" else [
            "house", "audio_supply", "interviews", "press", "press_delta"
        ],
        "inputs": press_inputs,
        "press_delta": press_delta_result,
    }
    if args.scope == "press":
        failed = bool(press_delta_result["errors"])
        audited_names = ("press_delta",)
    else:
        result["inputs"].update({
            "clip_manifest": str(args.clip_manifest.resolve()),
            "clip_manifest_sha256": sha256(args.clip_manifest),
        })
        result.update({
            "house": audit_house(args.house_root, args.clip_manifest, args.roster),
            "audio_supply": audit_audio_supply(
                args.clip_manifest, args.wj_root, args.axe_root, args.axe_features, args.db,
                args.youtube_root, args.audio_pilot,
            ),
            "interviews": audit_db(args.db),
            "press": audit_press(args.press),
        })
        failed = bool(
            result["house"]["errors"]
            or result["house"]["all_clip_errors"]
            or result["audio_supply"]["errors"]
            or result["interviews"]["errors"]
            or result["press"]["errors"]
            or result["press_delta"]["errors"]
        )
        audited_names = ("house", "audio_supply", "interviews", "press", "press_delta")
    # Negative receipts are useful audit evidence, but must be unmistakable and impossible for
    # downstream publishers to accept as a successful collection gate.
    result["status"] = "REJECTED" if failed else "ACCEPTED"
    # house.all_clip_errors is part of the REJECT decision above but was not part of this tally, so
    # a run rejected solely on accepted-clip integrity published status=REJECTED with
    # error_count=0 -- a receipt that reads clean to anything summarising the count.
    result["error_count"] = sum(
        len((result[name] or {}).get("errors") or []) +
        len((result[name] or {}).get("all_clip_errors") or []) for name in audited_names
    )
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(payload, end="")
    if args.output:
        # Preserve the previously accepted run before replacing the rolling gate
        # receipt.  This is the migration seam that keeps the already-active run
        # available when the next accepted run becomes current.
        if args.output.is_file():
            try:
                previous = json.loads(args.output.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError("existing collection intake receipt is unreadable") from exc
            archive_accepted_press_receipt(previous, args.accepted_press_receipts_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(str(args.output) + ".tmp")
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        temporary.replace(args.output)
        archive_accepted_press_receipt(result, args.accepted_press_receipts_dir)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
