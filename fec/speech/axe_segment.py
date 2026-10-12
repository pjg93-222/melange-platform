#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Segment and featurize the five deterministically bound Axe Files pilot episodes.

The published member turns in ``social_media.db`` are bound to manifest rows by
the exact episode GUID at the end of ``speeches.package``. The manifest supplies
the WAV path; its basename without ``.wav`` is emitted as ``sid`` for
``cut_spans.py`` compatibility.

Examples:
    python axe_segment.py --limit 1
    python axe_segment.py --episode ac039188-cdf2-4fc7-b3e5-b00c017d18d0
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import types
import warnings
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Sequence


for _variable in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_variable] = "1"

warnings.filterwarnings("ignore")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="ascii", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

# Keep the proven NPR environment shim: soundfile supplies audio decoding here.
_av = types.ModuleType("av")
_av.audio = types.ModuleType("av.audio")
_av.error = types.ModuleType("av.error")


class _Blocked(Exception):
    pass


_av.error.InvalidDataError = _Blocked
_av.open = None
sys.modules.setdefault("av", _av)
sys.modules.setdefault("av.audio", _av.audio)
sys.modules.setdefault("av.error", _av.error)

import numpy as np
import opensmile
import soundfile as sf
from faster_whisper import WhisperModel


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import ROOT as _MB_ROOT  # noqa: E402

# Resolve the project root the way every other producer does. The hardcoded literal made
# this writer emit outputs/axe_pilot_20260827/axe_features.jsonl into the main checkout
# while its consumer, build_descriptive_speech.feature_paths(), reads that file relative
# to paths.ROOT -- so a worktree (or any MONEYBALL_ROOT override) silently diverged.
BASE = Path(_MB_ROOT)
DB_PATH = BASE / "social_media.db"
SOURCE_ROOT = Path(r"C:\melange-data\axe_audio")
MANIFEST_PATH = SOURCE_ROOT / "axe_manifest.jsonl"
MODEL_PATH = r"C:\melange-data\models\faster-whisper-small"
OUTPUT_DIR = BASE / "outputs" / "axe_pilot_20260827"
FEATURE_PATH = OUTPUT_DIR / "axe_features.jsonl"

PROMPT = "Um, uh, so, you know, I mean, like, uh, well, um, I think, you know what I mean, uh..."
MIN_SEG = 8.0
MIN_MATCH = 0.60
FILLERS = {"uh", "um", "erm"}
PACKAGE_PREFIX = "https://www.cnn.com/audio/podcasts/axe-files/episodes/"
FEATURE_COLUMNS = {
    "pause": "MeanUnvoicedSegmentLength",
    "pace": "VoicedSegmentsPerSec",
    "volume_dyn": "loudness_sma3_stddevNorm",
    "strain": "jitterLocal_sma3nz_amean",
    "pitch_var": "F0semitoneFrom27.5Hz_sma3nz_stddevNorm",
    "f0_mean": "F0semitoneFrom27.5Hz_sma3nz_amean",
}

# This is the complete pilot allowlist. Member and date are binding assertions,
# not fuzzy lookup hints.
PILOT_EPISODES = (
    {
        "guid": "ac039188-cdf2-4fc7-b3e5-b00c017d18d0",
        "member": "Mark Kelly",
        "date": "2023-05-25",
    },
    {
        "guid": "c39ab944-4576-4d99-8ada-b045000c1277",
        "member": "Tammy Baldwin",
        "date": "2023-07-20",
    },
    {
        "guid": "ce60c69a-9f76-11ee-84b4-472857128c4c",
        "member": "Dan Crenshaw",
        "date": "2024-07-25",
    },
    {
        "guid": "d019b3d4-9f76-11ee-84b4-7bab49e6f4f4",
        "member": "Nancy Pelosi",
        "date": "2024-08-29",
    },
    {
        "guid": "d51bd830-9f76-11ee-84b4-df852c827ef7",
        "member": "Marie Gluesenkamp Perez",
        "date": "2024-12-05",
    },
)


class BindingError(RuntimeError):
    """An expected pilot episode could not be bound exactly."""


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Segment the five deterministically bound Axe Files pilot episodes."
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        help="Process only the first N episodes from the fixed pilot allowlist.",
    )
    parser.add_argument(
        "--episode",
        metavar="GUID",
        help="Process one GUID from the fixed pilot allowlist.",
    )
    args = parser.parse_args(argv)
    known = {episode["guid"] for episode in PILOT_EPISODES}
    if args.episode is not None and args.episode not in known:
        parser.error("--episode must be one of the five pilot GUIDs")
    return args


def normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def norm(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def selected_specs(args: argparse.Namespace) -> list[dict[str, str]]:
    specs = [dict(episode) for episode in PILOT_EPISODES]
    if args.episode is not None:
        specs = [episode for episode in specs if episode["guid"] == args.episode]
    if args.limit is not None:
        specs = specs[: args.limit]
    return specs


def load_manifest() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with MANIFEST_PATH.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BindingError(
                    "manifest line %d is invalid JSON: %s" % (line_number, exc.msg)
                ) from exc
            if not isinstance(row, dict):
                raise BindingError("manifest line %d is not an object" % line_number)
            rows.append(row)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(row: dict[str, Any]) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(payload)


def load_speech_rows() -> list[tuple[int, str, str, str, str]]:
    uri = "file:" + str(DB_PATH).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        return connection.execute(
            "SELECT rowid, member, date, package, text FROM speeches "
            "WHERE source = 'INTERVIEW' AND chamber = 'axefiles' ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()


def bind_episode(
    spec: dict[str, str],
    manifest_rows: Sequence[dict[str, Any]],
    speech_rows: Sequence[tuple[int, str, str, str, str]],
) -> dict[str, Any]:
    guid = spec["guid"]
    expected_package = PACKAGE_PREFIX + guid
    turns = [row for row in speech_rows if row[3] == expected_package]
    if not turns:
        raise BindingError("no speech rows for exact package " + expected_package)
    for rowid, member, date, package, text in turns:
        if member != spec["member"] or date != spec["date"] or package != expected_package:
            raise BindingError("speech row member/date/package assertion failed")
        if not isinstance(text, str) or not text.strip():
            raise BindingError("speech row has empty or non-text transcript")

    hits = [row for row in manifest_rows if row.get("guid") == guid]
    if len(hits) != 1:
        raise BindingError("expected one manifest GUID match, found %d" % len(hits))
    manifest = hits[0]
    if manifest.get("member") != spec["member"]:
        raise BindingError("manifest member does not equal speech member")

    pub_date = manifest.get("pubDate")
    if not isinstance(pub_date, str):
        raise BindingError("manifest pubDate is missing or non-text")
    try:
        manifest_date = parsedate_to_datetime(pub_date).date().isoformat()
    except (TypeError, ValueError, OverflowError) as exc:
        raise BindingError("manifest pubDate cannot be parsed") from exc
    if manifest_date != spec["date"]:
        raise BindingError("manifest pubDate does not equal speech date")

    wav_value = manifest.get("wav")
    if not isinstance(wav_value, str) or not wav_value:
        raise BindingError("manifest wav is missing or non-text")
    wav = Path(wav_value)
    if wav.suffix.casefold() != ".wav":
        raise BindingError("manifest wav does not end in .wav")
    if normalized_path(wav.parent) != normalized_path(SOURCE_ROOT):
        raise BindingError("manifest wav is outside the Axe audio root")
    if not wav.is_file():
        raise BindingError("manifest wav does not exist: " + str(wav))

    sid = wav.stem
    if not sid or Path(sid).name != sid:
        raise BindingError("manifest wav basename cannot be used as sid")
    return {
        "guid": guid,
        "member": spec["member"],
        "date": spec["date"],
        "package": expected_package,
        "wav": wav,
        "sid": sid,
        "turns": [
            {
                "rowid": row[0],
                "member": row[1],
                "date": row[2],
                "package": row[3],
                "text": row[4],
            }
            for row in turns
        ],
        "source_audio_sha256": sha256_file(wav),
        "source_manifest_sha256": sha256_json(manifest),
    }


def load_done_counts() -> collections.Counter[str]:
    counts: collections.Counter[str] = collections.Counter()
    if not FEATURE_PATH.exists():
        return counts
    with FEATURE_PATH.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "existing feature line %d is invalid JSON: %s" % (line_number, exc.msg)
                ) from exc
            sid = row.get("sid") if isinstance(row, dict) else None
            if not isinstance(sid, str) or not sid:
                raise RuntimeError("existing feature line %d has no string sid" % line_number)
            counts[sid] += 1
    return counts


def detect_repetition(
    guid: str, words: Sequence[tuple[str, float, float]], tokens: Sequence[str]
) -> None:
    # A repeated longer gram necessarily repeats its first four tokens, so the
    # four-gram check also trips on all 4+-gram repetition loops.
    timed_tokens = [(token, words[index][1]) for index, token in enumerate(tokens) if token]
    occurrences: dict[tuple[str, str, str, str], collections.deque[float]] = (
        collections.defaultdict(collections.deque)
    )
    reported: set[tuple[str, str, str, str]] = set()
    for index in range(max(0, len(timed_tokens) - 3)):
        gram = tuple(item[0] for item in timed_tokens[index : index + 4])
        timestamp = timed_tokens[index][1]
        window = occurrences[gram]
        while window and timestamp - window[0] > 60.0:
            window.popleft()
        window.append(timestamp)
        if len(window) > 6 and gram not in reported:
            reported.add(gram)
            print(
                "LOOP-SUSPECT %s at=%.1fs count=%d gram=%s"
                % (guid, timestamp, len(window), " ".join(gram)),
                flush=True,
            )


def align_turns(
    turns: Sequence[dict[str, Any]],
    words: Sequence[tuple[str, float, float]],
    tokens: Sequence[str],
) -> list[tuple[float, float, float, int, int, int]]:
    content_indexes = [
        index for index, token in enumerate(tokens) if token and token not in FILLERS
    ]
    pointer = 0
    spans: list[tuple[float, float, float, int, int, int]] = []
    for turn_index, turn in enumerate(turns):
        turn_tokens = [token for token in norm(turn["text"]) if token not in FILLERS]
        if len(turn_tokens) < 12:
            continue
        needed = set(turn_tokens)
        length = len(turn_tokens)
        best_score = 0.0
        best_index: int | None = None
        high = min(len(content_indexes), pointer + 12 * length + 400)
        for start in range(pointer, max(pointer, high - length) + 1):
            window = content_indexes[start : start + length]
            if len(window) < length * 0.6:
                break
            hits = sum(1 for word_index in window if tokens[word_index] in needed)
            score = hits / float(length)
            if score > best_score:
                best_score = score
                best_index = start
        if best_index is not None and best_score >= MIN_MATCH:
            first_word = content_indexes[best_index]
            last_word = content_indexes[
                min(best_index + length - 1, len(content_indexes) - 1)
            ]
            spans.append(
                (
                    words[first_word][1],
                    words[last_word][2],
                    best_score,
                    first_word,
                    last_word,
                    turn_index,
                )
            )
            pointer = best_index + length
    return spans


def feature_records(
    episode: dict[str, Any],
    audio: np.ndarray,
    sample_rate: int,
    words: Sequence[tuple[str, float, float]],
    tokens: Sequence[str],
    spans: Sequence[tuple[float, float, float, int, int, int]],
    smile: opensmile.Smile,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for segment_index, (start, end, score, first_word, last_word, turn_index) in enumerate(spans):
        if end - start < MIN_SEG:
            continue
        signal = audio[int(start * sample_rate) : int(end * sample_rate)]
        if len(signal) < MIN_SEG * sample_rate:
            continue
        try:
            features = smile.process_signal(signal, sample_rate).iloc[0]
        except Exception as exc:
            print(
                "FEATURE-FAIL %s seg=%d error=%s"
                % (episode["guid"], segment_index, str(exc)[:100]),
                flush=True,
            )
            continue
        span_words = [
            tokens[index]
            for index in range(first_word, last_word + 1)
            if tokens[index]
        ]
        word_count = len([token for token in span_words if token not in FILLERS])
        hesitation_count = sum(1 for token in span_words if token in FILLERS)
        record: dict[str, Any] = {
            "sid": episode["sid"],
            "member": episode["member"],
            "seg": segment_index,
            "start": round(start, 1),
            "end": round(end, 1),
            "dur": round(end - start, 1),
            "match": round(score, 3),
            "words": word_count,
            "hes_1k": round(1000.0 * hesitation_count / max(word_count, 1), 2),
        }
        for output_name, feature_name in FEATURE_COLUMNS.items():
            record[output_name] = round(float(features[feature_name]), 5)
        turn = episode["turns"][turn_index]
        rowid = int(turn["rowid"])
        record.update(
            {
                "provenance_schema": "axe-clip-turn-v1",
                "artifact_id": f"axe:{episode['guid']}:speeches:{rowid}:seg:{segment_index}",
                "episode_guid": episode["guid"],
                "turn_id": f"sqlite:speeches:{rowid}",
                "package": episode["package"],
                "turn_date": episode["date"],
                "transcript_path": str(DB_PATH.resolve()) + f"#speeches.rowid={rowid}",
                "text_sha256": sha256_text(str(turn["text"])),
                "source_audio_sha256": episode["source_audio_sha256"],
                "source_manifest_sha256": episode["source_manifest_sha256"],
                "binding_method": "exact_guid_package_member_date+whisper_monotonic_alignment",
            }
        )
        record["feature_sha256"] = sha256_json(record)
        records.append(record)
    return records


def process_episode(
    episode: dict[str, Any],
    whisper: WhisperModel,
    smile: opensmile.Smile,
) -> tuple[float, int, list[dict[str, Any]], bool]:
    try:
        audio, sample_rate = sf.read(
            str(episode["wav"]), dtype="float32", always_2d=False
        )
    except Exception as exc:
        print(
            "AUDIO-FAIL %s error=%s" % (episode["guid"], str(exc)[:100]), flush=True
        )
        return 0.0, 0, [], True
    if audio.ndim == 2:
        audio = audio.mean(axis=1)

    decode_started = time.monotonic()
    try:
        segment_iterator, _ = whisper.transcribe(
            audio,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            word_timestamps=True,
            initial_prompt=PROMPT,
            condition_on_previous_text=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 700},
        )
        whisper_segments = list(segment_iterator)
    except Exception as exc:
        decode_minutes = (time.monotonic() - decode_started) / 60.0
        print(
            "DECODE-FAIL %s error=%s" % (episode["guid"], str(exc)[:100]), flush=True
        )
        return decode_minutes, 0, [], True
    decode_minutes = (time.monotonic() - decode_started) / 60.0

    words = [
        (word.word, word.start, word.end)
        for segment in whisper_segments
        for word in (segment.words or [])
    ]
    tokens = [norm(word[0])[0] if norm(word[0]) else "" for word in words]
    detect_repetition(episode["guid"], words, tokens)
    if len(words) < 40:
        print("DECODE-SHORT %s words=%d" % (episode["guid"], len(words)), flush=True)
        return decode_minutes, len(whisper_segments), [], True

    spans = align_turns(episode["turns"], words, tokens)
    records = feature_records(
        episode, audio, sample_rate, words, tokens, spans, smile
    )
    return decode_minutes, len(whisper_segments), records, False


def append_records(records: Sequence[dict[str, Any]]) -> None:
    if not records:
        return
    with FEATURE_PATH.open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        handle.flush()


def print_progress(
    guid: str, member: str, decode_minutes: float, spans: int, matched: int
) -> None:
    print(
        "EPISODE %s %s decode=%.2f spans=%d matched=%d"
        % (guid, member, decode_minutes, spans, matched),
        flush=True,
    )


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    specs = selected_specs(args)
    try:
        manifest_rows = load_manifest()
        speech_rows = load_speech_rows()
    except (BindingError, OSError, sqlite3.Error) as exc:
        print("ERROR binding-input %s" % str(exc), file=sys.stderr, flush=True)
        return 2

    episodes: list[dict[str, Any]] = []
    failures = 0
    for spec in specs:
        try:
            episodes.append(bind_episode(spec, manifest_rows, speech_rows))
        except (BindingError, OSError) as exc:
            failures += 1
            print(
                "REFUSE %s %s reason=%s"
                % (spec["guid"], spec["member"], str(exc)),
                file=sys.stderr,
                flush=True,
            )
            print_progress(spec["guid"], spec["member"], 0.0, 0, 0)

    if not episodes:
        return 2
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        done_counts = load_done_counts()
    except (OSError, RuntimeError) as exc:
        print("ERROR existing-output %s" % str(exc), file=sys.stderr, flush=True)
        return 2

    pending: list[dict[str, Any]] = []
    for episode in episodes:
        if done_counts[episode["sid"]]:
            print(
                "SKIP %s already-done rows=%d"
                % (episode["guid"], done_counts[episode["sid"]]),
                flush=True,
            )
            print_progress(
                episode["guid"], episode["member"], 0.0, 0, done_counts[episode["sid"]]
            )
        else:
            pending.append(episode)
    if not pending:
        return 2 if failures else 0

    smile = opensmile.Smile(
        feature_set=opensmile.FeatureSet.eGeMAPSv02,
        feature_level=opensmile.FeatureLevel.Functionals,
    )
    whisper = WhisperModel(
        MODEL_PATH,
        device="cpu",
        compute_type="int8",
        local_files_only=True,
        cpu_threads=1,
        num_workers=1,
    )

    for episode in pending:
        decode_minutes, emitted_segments, records, failed = process_episode(
            episode, whisper, smile
        )
        if failed:
            failures += 1
        try:
            append_records(records)
        except OSError as exc:
            failures += 1
            records = []
            print(
                "WRITE-FAIL %s error=%s" % (episode["guid"], str(exc)[:100]),
                file=sys.stderr,
                flush=True,
            )
        print_progress(
            episode["guid"],
            episode["member"],
            decode_minutes,
            emitted_segments,
            len(records),
        )
    return 2 if failures else 0


def main() -> int:
    try:
        return run()
    except KeyboardInterrupt:
        print("ERROR interrupted", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
