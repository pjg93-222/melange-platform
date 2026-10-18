#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cut member speech spans from WAV files without loading whole recordings.

The manifest is written to ``<out parent>/clips_manifest.jsonl`` so separate
``npr`` and ``sunday`` output directories can share the requested manifest.
Conflicting payloads collapsed by the mandated dedupe key are preserved in
``<out parent>/clips_conflicts.jsonl`` before audio preparation begins. Dry-run
mode performs all input validation, ``sf.info`` calls, bounded audio reads,
clamping, and RMS checks. It writes the diagnostic conflicts sidecar, but no
audio or manifest.

Examples:
    python cut_spans.py --features "outputs/npr*/npr_features_*.jsonl" \
        --wav-root "C:/melange-data/npr_audio" \
        --out "C:/melange-data/member_clips/npr" --outlet NPR --dry-run

    python cut_spans.py --features "outputs/sunday*/sunday_features_*.jsonl" \
        --wav-root "C:/melange-data/sunday_audio" \
        --out "C:/melange-data/member_clips/sunday" --outlet Sunday --dry-run
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np
import soundfile as sf


REQUIRED_KEYS = frozenset(
    {
        "sid",
        "member",
        "seg",
        "start",
        "end",
        "dur",
        "match",
        "words",
        "hes_1k",
        "pause",
        "pace",
        "volume_dyn",
        "strain",
        "pitch_var",
        "f0_mean",
    }
)

SHOW_BY_PREFIX = {
    "face_": "CBS Face the Nation",
    "meet_": "NBC Meet the Press",
    "sotu_": "CNN State of the Union",
    "this_": "ABC This Week",
}

WINDOWS_INVALID = frozenset('<>:"/\\|?*')
WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
MAX_SLUG_LENGTH = 96
PROVENANCE_KEYS = (
    "provenance_schema",
    "artifact_id",
    "episode_guid",
    "turn_id",
    "package",
    "turn_date",
    "transcript_path",
    "text_sha256",
    "source_audio_sha256",
    "source_manifest_sha256",
    "feature_sha256",
    "binding_method",
)


class CutSpansError(RuntimeError):
    """An input, safety, or output error that should terminate the run."""


@dataclass(frozen=True)
class SpanRow:
    sid: str
    member: str
    seg: int
    start: float
    end: float
    dur: float
    match: float
    words: int
    source_file: Path
    line_number: int
    payload: dict[str, Any]

    @property
    def provenance(self) -> str:
        return f"{self.source_file}:{self.line_number}"


@dataclass(frozen=True)
class LoadStats:
    physical_rows: int
    rows_after_match: int
    duration_mismatch_rows: int


@dataclass(frozen=True)
class PreparedSpan:
    row: SpanRow
    member_slug: str
    src_wav: Path
    out_wav: Path
    show: str
    samplerate: int
    start_frame: int
    frames: int
    start: float
    end: float
    dur: float


@dataclass
class RunSummary:
    clips_processed: int = 0
    clips_written: int = 0
    total_bytes: int = 0
    total_seconds: float = 0.0
    rms_zero_count: int = 0
    skipped_span_count: int = 0
    members: set[str] | None = None
    show_counts: Counter[str] | None = None

    def __post_init__(self) -> None:
        if self.members is None:
            self.members = set()
        if self.show_counts is None:
            self.show_counts = Counter()


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
        description="Seek-read member speech spans and write standalone WAV clips."
    )
    parser.add_argument(
        "--features",
        required=True,
        help="Quoted glob matching one or more feature JSONL files.",
    )
    parser.add_argument(
        "--wav-root", required=True, type=Path, help="Directory containing <sid>.wav files."
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help=(
            "Clip output directory. The atomic manifest is written to this "
            "directory's parent as clips_manifest.jsonl."
        ),
    )
    parser.add_argument(
        "--outlet",
        required=True,
        help="Manifest outlet value. NPR is canonicalized to uppercase NPR.",
    )
    parser.add_argument(
        "--min-match",
        type=float,
        default=0.90,
        help="Minimum inclusive match score (default: 0.90).",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        help="Process only the first N rows after filtering and deduplication.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Read and validate clips and compute RMS without writing audio or manifest; "
            "the diagnostic conflicts sidecar is still written."
        ),
    )
    return parser.parse_args(argv)


def finite_number(value: Any, field: str, provenance: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CutSpansError(f"{provenance}: {field!r} must be numeric, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise CutSpansError(f"{provenance}: {field!r} must be finite, got {value!r}")
    return number


def integral_number(value: Any, field: str, provenance: str) -> int:
    number = finite_number(value, field, provenance)
    if not number.is_integer():
        raise CutSpansError(f"{provenance}: {field!r} must be an integer, got {value!r}")
    return int(number)


def feature_files(pattern: str) -> list[Path]:
    matches = [Path(item).resolve() for item in glob.glob(pattern, recursive=True)]
    unique: dict[str, Path] = {}
    for path in matches:
        if not path.is_file():
            raise CutSpansError(f"Feature glob matched a non-file path: {path}")
        unique[os.path.normcase(str(path))] = path
    files = sorted(unique.values(), key=lambda path: os.path.normcase(str(path)))
    if not files:
        raise CutSpansError(f"Feature glob matched no files: {pattern!r}")
    return files


def parse_span(payload: Any, source_file: Path, line_number: int) -> SpanRow:
    provenance = f"{source_file}:{line_number}"
    if not isinstance(payload, dict):
        raise CutSpansError(f"{provenance}: JSON value must be an object")
    missing = sorted(REQUIRED_KEYS.difference(payload))
    if missing:
        raise CutSpansError(f"{provenance}: missing required keys: {', '.join(missing)}")

    sid = payload["sid"]
    member = payload["member"]
    if not isinstance(sid, str) or not sid:
        raise CutSpansError(f"{provenance}: 'sid' must be a nonempty string")
    if not isinstance(member, str) or not member.strip():
        raise CutSpansError(f"{provenance}: 'member' must be a nonempty string")

    seg = integral_number(payload["seg"], "seg", provenance)
    words = integral_number(payload["words"], "words", provenance)
    start = finite_number(payload["start"], "start", provenance)
    end = finite_number(payload["end"], "end", provenance)
    dur = finite_number(payload["dur"], "dur", provenance)
    match = finite_number(payload["match"], "match", provenance)

    if seg < 0:
        raise CutSpansError(f"{provenance}: 'seg' must be nonnegative, got {seg}")
    if words < 0:
        raise CutSpansError(f"{provenance}: 'words' must be nonnegative, got {words}")
    if end <= start:
        raise CutSpansError(
            f"{provenance}: span end must exceed start, got start={start}, end={end}"
        )
    if dur <= 0:
        raise CutSpansError(f"{provenance}: 'dur' must be positive, got {dur}")

    return SpanRow(
        sid=sid,
        member=member,
        seg=seg,
        start=start,
        end=end,
        dur=dur,
        match=match,
        words=words,
        source_file=source_file,
        line_number=line_number,
        payload=payload,
    )


def load_rows(files: Sequence[Path], min_match: float) -> tuple[list[SpanRow], LoadStats]:
    physical_rows = 0
    rows_after_match: list[SpanRow] = []
    duration_mismatch_rows = 0

    for path in files:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    print(f"WARNING blank-line {path}:{line_number}", flush=True)
                    continue
                physical_rows += 1
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise CutSpansError(
                        f"{path}:{line_number}: invalid JSON: {exc.msg} at column {exc.colno}"
                    ) from exc
                row = parse_span(payload, path, line_number)
                duration_delta = abs((row.end - row.start) - row.dur)
                if duration_delta > 0.05:
                    duration_mismatch_rows += 1
                if row.match >= min_match:
                    rows_after_match.append(row)

    if physical_rows == 0:
        raise CutSpansError("Feature files contained no nonblank JSONL rows")
    return rows_after_match, LoadStats(
        physical_rows=physical_rows,
        rows_after_match=len(rows_after_match),
        duration_mismatch_rows=duration_mismatch_rows,
    )


def deduplicate_rows(
    rows: Sequence[SpanRow],
) -> tuple[list[SpanRow], int, list[dict[str, Any]]]:
    groups: dict[tuple[str, str, float, float], list[SpanRow]] = {}
    key_order: list[tuple[str, str, float, float]] = []
    deduped: list[SpanRow] = []
    duplicates = 0
    conflicts: list[dict[str, Any]] = []

    for row in rows:
        key = (row.sid, row.member, row.start, row.end)
        if key not in groups:
            groups[key] = []
            key_order.append(key)
        groups[key].append(row)

    for key in key_order:
        group = groups[key]
        previous = group[0]
        deduped.append(previous)
        duplicates += len(group) - 1
        if any(previous.payload != row.payload for row in group[1:]):
            differing = sorted(
                field
                for field in set().union(*(row.payload.keys() for row in group))
                if len(
                    {
                        json.dumps(row.payload.get(field), ensure_ascii=False, sort_keys=True)
                        for row in group
                    }
                )
                > 1
            )
            conflicts.append(
                {
                    "sid": previous.sid,
                    "member": previous.member,
                    "seg": previous.seg,
                    "n_rows": len(group),
                    "rows": [row.payload for row in group],
                }
            )
            print(
                "WARNING conflicting-duplicate "
                f"key={key!r} keeping={previous.provenance} "
                f"dropping={[row.provenance for row in group[1:]]} "
                f"differing_fields={differing}",
                flush=True,
            )
    return deduped, duplicates, conflicts


def _is_control_character(character: str) -> bool:
    return unicodedata.category(character).startswith("C")


def base_member_slug(member: str) -> str:
    normalized = unicodedata.normalize("NFKC", member).strip()
    safe = "".join(
        "_" if character in WINDOWS_INVALID or _is_control_character(character) else character
        for character in normalized
    )
    safe = re.sub(r"\s+", "_", safe)
    safe = re.sub(r"_+", "_", safe).strip(" ._")
    if not safe:
        safe = "member"
    if safe.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        safe = f"_{safe}"
    if len(safe) > MAX_SLUG_LENGTH:
        suffix = "--" + hashlib.sha256(member.encode("utf-8")).hexdigest()[:8]
        safe = safe[: MAX_SLUG_LENGTH - len(suffix)].rstrip(" ._") + suffix
    return safe


def member_slugs(rows: Sequence[SpanRow]) -> dict[str, str]:
    members = sorted({row.member for row in rows})
    by_slug: dict[str, list[str]] = defaultdict(list)
    for member in members:
        by_slug[base_member_slug(member).casefold()].append(member)

    result: dict[str, str] = {}
    for colliding_members in by_slug.values():
        for member in colliding_members:
            base = base_member_slug(member)
            if len(colliding_members) == 1:
                result[member] = base
                continue
            suffix = "--" + hashlib.sha256(member.encode("utf-8")).hexdigest()[:8]
            trimmed = base[: MAX_SLUG_LENGTH - len(suffix)].rstrip(" ._")
            result[member] = (trimmed or "member") + suffix
            print(
                f"WARNING member-slug-collision member={member!r} slug={result[member]!r}",
                flush=True,
            )
    return result


def validate_sid_component(sid: str, provenance: str) -> None:
    unsafe = (
        sid in {".", ".."}
        or sid.endswith((" ", "."))
        or any(character in WINDOWS_INVALID or _is_control_character(character) for character in sid)
        or sid.split(".", 1)[0].upper() in WINDOWS_RESERVED
    )
    if unsafe:
        raise CutSpansError(
            f"{provenance}: sid is not a safe verbatim Windows filename component: {sid!r}"
        )


def show_for_wav(wav: Path, outlet: str) -> str:
    if outlet == "NPR":
        return "NPR"
    lowered = wav.name.casefold()
    for prefix, show in SHOW_BY_PREFIX.items():
        if lowered.startswith(prefix):
            return show
    if outlet.casefold() == "cnn":
        return "CNN The Axe Files"
    raise CutSpansError(
        f"Sunday/non-NPR WAV has no recognized show prefix: {wav.name!r}; "
        f"expected one of {sorted(SHOW_BY_PREFIX)}"
    )


def prepare_spans(
    rows: Sequence[SpanRow],
    wav_root: Path,
    out_dir: Path,
    outlet: str,
) -> tuple[list[PreparedSpan], int]:
    slugs = member_slugs(rows)
    info_cache: dict[Path, tuple[int, int]] = {}
    output_names: dict[str, SpanRow] = {}
    prepared: list[PreparedSpan] = []
    skipped = 0

    for row in rows:
        validate_sid_component(row.sid, row.provenance)
        src_wav = wav_root / f"{row.sid}.wav"
        if not src_wav.is_file():
            raise CutSpansError(f"{row.provenance}: source WAV does not exist: {src_wav}")
        src_wav = src_wav.resolve()

        if src_wav not in info_cache:
            try:
                info = sf.info(str(src_wav))
            except Exception as exc:
                raise CutSpansError(f"Could not inspect WAV {src_wav}: {exc}") from exc
            samplerate = int(info.samplerate)
            total_frames = int(info.frames)
            if samplerate <= 0 or total_frames <= 0:
                raise CutSpansError(
                    f"Invalid WAV metadata for {src_wav}: "
                    f"samplerate={samplerate}, frames={total_frames}"
                )
            info_cache[src_wav] = (samplerate, total_frames)

        samplerate, total_frames = info_cache[src_wav]
        source_seconds = total_frames / samplerate
        requested_start_frame = int(row.start * samplerate)
        requested_end_frame = int(row.end * samplerate)

        if row.start >= source_seconds or requested_start_frame >= total_frames:
            skipped += 1
            print(
                "SKIP start-beyond-EOF "
                f"{row.provenance} sid={row.sid!r} member={row.member!r} "
                f"start={row.start:.6f}s source_duration={source_seconds:.6f}s",
                flush=True,
            )
            continue

        start_frame = max(0, min(requested_start_frame, total_frames))
        end_frame = max(0, min(requested_end_frame, total_frames))
        if start_frame != requested_start_frame or end_frame != requested_end_frame:
            print(
                "CLAMP span "
                f"{row.provenance} sid={row.sid!r} member={row.member!r} "
                f"requested=({row.start:.6f},{row.end:.6f}) "
                f"actual=({start_frame / samplerate:.6f},{end_frame / samplerate:.6f}) "
                f"source_duration={source_seconds:.6f}",
                flush=True,
            )

        frames = end_frame - start_frame
        if frames <= 0:
            skipped += 1
            print(
                "SKIP empty-after-clamp "
                f"{row.provenance} sid={row.sid!r} member={row.member!r} "
                f"requested=({row.start:.6f},{row.end:.6f})",
                flush=True,
            )
            continue

        slug = slugs[row.member]
        start_ms = int(round(row.start * 1000))
        end_ms = int(round(row.end * 1000))
        filename = f"{slug}__{row.sid}__{row.seg:03d}__{start_ms}_{end_ms}.wav"
        if len(filename) > 240:
            raise CutSpansError(
                f"{row.provenance}: output filename is too long ({len(filename)} chars): {filename}"
            )
        out_wav = (out_dir / filename).resolve()
        output_key = os.path.normcase(str(out_wav))
        previous = output_names.get(output_key)
        if previous is not None:
            raise CutSpansError(
                "Distinct spans map to the same output filename: "
                f"{previous.provenance} and {row.provenance} -> {out_wav}"
            )
        output_names[output_key] = row

        prepared.append(
            PreparedSpan(
                row=row,
                member_slug=slug,
                src_wav=src_wav,
                out_wav=out_wav,
                show=show_for_wav(src_wav, outlet),
                samplerate=samplerate,
                start_frame=start_frame,
                frames=frames,
                start=start_frame / samplerate,
                end=end_frame / samplerate,
                dur=frames / samplerate,
            )
        )
    return prepared, skipped


def first_existing_wav(out_dir: Path) -> Path | None:
    if not out_dir.exists():
        return None
    if not out_dir.is_dir():
        raise CutSpansError(f"Output path exists but is not a directory: {out_dir}")
    for path in out_dir.rglob("*"):
        if path.is_file() and path.suffix.casefold() == ".wav":
            return path
    return None


def canonical_path_text(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(value)))


def load_existing_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    if not manifest_path.exists():
        return []
    if not manifest_path.is_file():
        raise CutSpansError(f"Manifest path exists but is not a file: {manifest_path}")

    rows: list[dict[str, Any]] = []
    seen_outputs: set[str] = set()
    with manifest_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CutSpansError(
                    f"Existing manifest {manifest_path}:{line_number} is invalid JSON: {exc.msg}"
                ) from exc
            if not isinstance(row, dict) or not isinstance(row.get("out_wav"), str):
                raise CutSpansError(
                    f"Existing manifest {manifest_path}:{line_number} lacks string out_wav"
                )
            output_key = canonical_path_text(row["out_wav"])
            if output_key in seen_outputs:
                raise CutSpansError(
                    f"Existing manifest contains duplicate out_wav at line {line_number}: "
                    f"{row['out_wav']}"
                )
            seen_outputs.add(output_key)
            rows.append(row)
    return rows


def reject_manifest_collisions(
    existing_rows: Sequence[dict[str, Any]], prepared: Sequence[PreparedSpan]
) -> None:
    existing_outputs = {canonical_path_text(row["out_wav"]) for row in existing_rows}
    collisions = [span.out_wav for span in prepared if canonical_path_text(span.out_wav) in existing_outputs]
    if collisions:
        raise CutSpansError(
            "Refusing to duplicate existing manifest output; first collision: " + str(collisions[0])
        )


def load_existing_conflicts(conflicts_path: Path) -> list[dict[str, Any]]:
    if not conflicts_path.exists():
        return []
    if not conflicts_path.is_file():
        raise CutSpansError(f"Conflicts path exists but is not a file: {conflicts_path}")

    rows: list[dict[str, Any]] = []
    expected_keys = {"sid", "member", "seg", "n_rows", "rows"}
    with conflicts_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CutSpansError(
                    f"Existing conflicts sidecar {conflicts_path}:{line_number} "
                    f"is invalid JSON: {exc.msg}"
                ) from exc
            if not isinstance(row, dict) or set(row) != expected_keys:
                raise CutSpansError(
                    f"Existing conflicts sidecar {conflicts_path}:{line_number} "
                    f"must contain exactly {sorted(expected_keys)}"
                )
            if not isinstance(row["rows"], list) or row["n_rows"] != len(row["rows"]):
                raise CutSpansError(
                    f"Existing conflicts sidecar {conflicts_path}:{line_number} "
                    "has inconsistent n_rows/rows"
                )
            rows.append(row)
    return rows


def merge_conflicts(
    existing_rows: Sequence[dict[str, Any]],
    new_rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    merged = list(existing_rows)
    signatures = {
        json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
        for row in existing_rows
    }
    added = 0
    for row in new_rows:
        signature = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if signature in signatures:
            continue
        signatures.add(signature)
        merged.append(row)
        added += 1
    return merged, added


@contextmanager
def manifest_lock(manifest_path: Path) -> Iterator[None]:
    lock_path = Path(str(manifest_path) + ".lock")
    try:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise CutSpansError(
            f"Manifest lock already exists; another run or a stale lock must be resolved: {lock_path}"
        ) from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_row(
    span: PreparedSpan,
    outlet: str,
    byte_count: int,
    rms: float,
    sha256: str,
) -> dict[str, Any]:
    result = {
        "member": span.row.member,
        "member_slug": span.member_slug,
        "outlet": outlet,
        "show": span.show,
        "sid": span.row.sid,
        "seg": span.row.seg,
        "start": round(span.start, 9),
        "end": round(span.end, 9),
        "dur": round(span.dur, 9),
        "match": span.row.match,
        "words": span.row.words,
        "src_wav": str(span.src_wav),
        "out_wav": str(span.out_wav),
        "bytes": byte_count,
        "samplerate": span.samplerate,
        "frames": span.frames,
        "rms": rms,
        "sha256": sha256,
    }
    for key in PROVENANCE_KEYS:
        if key in span.row.payload:
            result[key] = span.row.payload[key]
    return result


def process_spans(
    prepared: Sequence[PreparedSpan],
    outlet: str,
    dry_run: bool,
    skipped: int,
    new_manifest_rows: list[dict[str, Any]] | None = None,
) -> tuple[RunSummary, list[dict[str, Any]]]:
    summary = RunSummary(skipped_span_count=skipped)
    # Caller may pass its own list so rows for clips already written survive a mid-run error.
    if new_manifest_rows is None:
        new_manifest_rows = []

    for index, span in enumerate(prepared, 1):
        try:
            audio, read_samplerate = sf.read(
                str(span.src_wav),
                start=span.start_frame,
                frames=span.frames,
                dtype="int16",
                always_2d=False,
            )
        except Exception as exc:
            raise CutSpansError(
                f"Bounded read failed for {span.row.provenance} from {span.src_wav}: {exc}"
            ) from exc

        audio = np.asarray(audio)
        read_samplerate = int(read_samplerate)
        actual_frames = int(audio.shape[0]) if audio.ndim >= 1 else 0
        if read_samplerate != span.samplerate:
            raise CutSpansError(
                f"Samplerate changed between sf.info and sf.read for {span.src_wav}: "
                f"{span.samplerate} != {read_samplerate}"
            )
        if actual_frames != span.frames:
            raise CutSpansError(
                f"Short bounded read for {span.src_wav}: expected {span.frames} frames, "
                f"received {actual_frames}"
            )

        normalized = audio.astype(np.float64) / 32768.0
        rms = float(np.sqrt(np.mean(np.square(normalized))))
        if rms == 0.0:
            summary.rms_zero_count += 1
            print(
                "FAILURE digital-silence "
                f"sid={span.row.sid!r} member={span.row.member!r} seg={span.row.seg} "
                f"start={span.start:.6f} end={span.end:.6f} src={span.src_wav}",
                file=sys.stderr,
                flush=True,
            )

        byte_count = 0
        digest = ""
        if not dry_run:
            if span.out_wav.exists():
                raise CutSpansError(f"Refusing to overwrite output WAV: {span.out_wav}")
            temp_wav = span.out_wav.with_name(span.out_wav.name + ".tmp.wav")
            if temp_wav.exists():
                raise CutSpansError(f"Temporary output already exists: {temp_wav}")
            try:
                sf.write(str(temp_wav), audio, read_samplerate)
                os.replace(temp_wav, span.out_wav)
            except Exception as exc:
                temp_wav.unlink(missing_ok=True)
                raise CutSpansError(f"Could not atomically write {span.out_wav}: {exc}") from exc
            byte_count = span.out_wav.stat().st_size
            digest = sha256_file(span.out_wav)
            summary.clips_written += 1
            summary.total_bytes += byte_count
            new_manifest_rows.append(manifest_row(span, outlet, byte_count, rms, digest))

        summary.clips_processed += 1
        summary.total_seconds += span.dur
        assert summary.members is not None
        assert summary.show_counts is not None
        summary.members.add(span.row.member)
        summary.show_counts[span.show] += 1

        if index % 100 == 0 or index == len(prepared):
            verb = "checked" if dry_run else "written"
            print(f"PROGRESS clips-{verb}={index}/{len(prepared)}", flush=True)

    return summary, new_manifest_rows


def write_manifest_atomic(
    manifest_path: Path,
    existing_rows: Sequence[dict[str, Any]],
    new_rows: Sequence[dict[str, Any]],
) -> None:
    temp_path = Path(str(manifest_path) + ".tmp")
    if temp_path.exists():
        raise CutSpansError(
            f"Manifest temporary file already exists and will not be clobbered: {temp_path}"
        )
    try:
        with temp_path.open("x", encoding="utf-8", newline="\n") as handle:
            for row in list(existing_rows) + list(new_rows):
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, manifest_path)
    except Exception as exc:
        raise CutSpansError(f"Atomic manifest update failed for {manifest_path}: {exc}") from exc


def write_conflicts_atomic(
    conflicts_path: Path,
    rows: Sequence[dict[str, Any]],
) -> None:
    temp_path = Path(str(conflicts_path) + ".tmp")
    if temp_path.exists():
        raise CutSpansError(
            f"Conflicts temporary file already exists and will not be clobbered: {temp_path}"
        )
    try:
        with temp_path.open("x", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, conflicts_path)
    except Exception as exc:
        raise CutSpansError(f"Atomic conflicts update failed for {conflicts_path}: {exc}") from exc


def print_prefix_mapping() -> None:
    print("SHOW PREFIX MAPPING", flush=True)
    for prefix, show in SHOW_BY_PREFIX.items():
        print(f"  {prefix} -> {show}", flush=True)
    print("  NPR outlet -> NPR", flush=True)


def print_pipeline_counts(
    stats: LoadStats,
    rows_after_dedupe: int,
    duplicates_removed: int,
    conflicting_groups: int,
    selected_rows: int,
    limit: int | None,
) -> None:
    print("PIPELINE COUNTS", flush=True)
    print(f"  physical rows: {stats.physical_rows}", flush=True)
    print(f"  rows after match filter: {stats.rows_after_match}", flush=True)
    print(f"  rows dropped by match filter: {stats.physical_rows - stats.rows_after_match}", flush=True)
    print(f"  rows after dedupe: {rows_after_dedupe}", flush=True)
    print(f"  duplicates removed: {duplicates_removed}", flush=True)
    print(f"  conflicting duplicate groups: {conflicting_groups}", flush=True)
    print(
        f"  WARNING duration-mismatch rows (>0.05s): {stats.duration_mismatch_rows}",
        flush=True,
    )
    if limit is not None:
        print(f"  rows selected after --limit {limit}: {selected_rows}", flush=True)
    else:
        print(f"  rows selected for clip processing: {selected_rows}", flush=True)


def print_final_summary(summary: RunSummary, dry_run: bool) -> None:
    assert summary.members is not None
    assert summary.show_counts is not None
    print("FINAL SUMMARY", flush=True)
    print(f"  mode: {'DRY-RUN' if dry_run else 'WRITE'}", flush=True)
    print(f"  clips written: {summary.clips_written}", flush=True)
    print(f"  clips {'checked' if dry_run else 'processed'}: {summary.clips_processed}", flush=True)
    print(f"  total bytes: {summary.total_bytes}", flush=True)
    print(f"  total hours: {summary.total_seconds / 3600.0:.9f}", flush=True)
    print(f"  distinct members: {len(summary.members)}", flush=True)
    print("  per-show counts:", flush=True)
    if summary.show_counts:
        for show, count in sorted(summary.show_counts.items()):
            print(f"    {show}: {count}", flush=True)
    else:
        print("    (none)", flush=True)
    print(f"  RMS-zero count: {summary.rms_zero_count}", flush=True)
    print(f"  skipped-span count: {summary.skipped_span_count}", flush=True)
    if dry_run:
        print(
            "  dry-run wrote no audio or manifest; conflicts sidecar is diagnostic output",
            flush=True,
        )


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not math.isfinite(args.min_match) or not 0.0 <= args.min_match <= 1.0:
        raise CutSpansError(f"--min-match must be finite and within [0, 1], got {args.min_match}")

    outlet_text = args.outlet.strip()
    if not outlet_text:
        raise CutSpansError("--outlet must not be empty")
    outlet = "NPR" if outlet_text.casefold() == "npr" else outlet_text

    wav_root = args.wav_root.resolve()
    out_dir = args.out.resolve()
    manifest_path = out_dir.parent / "clips_manifest.jsonl"
    conflicts_path = out_dir.parent / "clips_conflicts.jsonl"
    if not wav_root.is_dir():
        raise CutSpansError(f"WAV root does not exist or is not a directory: {wav_root}")

    existing_wav = first_existing_wav(out_dir)
    if existing_wav is not None and not args.dry_run:
        raise CutSpansError(
            f"Refusing write run because --out already contains WAV files; "
            f"first found: {existing_wav}. Use --dry-run to inspect safely."
        )
    if existing_wav is not None and args.dry_run:
        print(
            f"DRY-RUN NOTE existing output WAVs are untouched; first found: {existing_wav}",
            flush=True,
        )

    print(f"features glob: {args.features}", flush=True)
    print(f"wav root: {wav_root}", flush=True)
    print(f"clip output: {out_dir}", flush=True)
    print(f"manifest: {manifest_path}", flush=True)
    print(f"conflicts sidecar: {conflicts_path}", flush=True)
    print(f"outlet: {outlet}", flush=True)
    print(f"minimum match: {args.min_match:.6f}", flush=True)
    print_prefix_mapping()

    files = feature_files(args.features)
    print(f"feature files matched: {len(files)}", flush=True)
    rows_after_match, stats = load_rows(files, args.min_match)
    deduped_rows, duplicates_removed, conflicting_groups = deduplicate_rows(rows_after_match)
    selected_rows = deduped_rows[: args.limit] if args.limit is not None else deduped_rows
    print_pipeline_counts(
        stats,
        rows_after_dedupe=len(deduped_rows),
        duplicates_removed=duplicates_removed,
        conflicting_groups=len(conflicting_groups),
        selected_rows=len(selected_rows),
        limit=args.limit,
    )

    conflicts_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_lock(manifest_path):
        temp_conflicts = Path(str(conflicts_path) + ".tmp")
        if temp_conflicts.exists():
            raise CutSpansError(
                f"Refusing to proceed while a conflicts temporary file exists: {temp_conflicts}"
            )
        sidecar_existed = conflicts_path.exists()
        existing_conflicts = load_existing_conflicts(conflicts_path)
        merged_conflicts, conflicts_added = merge_conflicts(
            existing_conflicts, conflicting_groups
        )
        if conflicts_added or not sidecar_existed:
            write_conflicts_atomic(conflicts_path, merged_conflicts)
        print(
            f"conflicts sidecar materialized: {conflicts_path} "
            f"(groups={len(merged_conflicts)}, added={conflicts_added})",
            flush=True,
        )

    prepared, skipped = prepare_spans(selected_rows, wav_root, out_dir, outlet)

    if args.dry_run:
        summary, _ = process_spans(prepared, outlet, dry_run=True, skipped=skipped)
        print_final_summary(summary, dry_run=True)
        if summary.rms_zero_count:
            return 3
        if summary.clips_processed == 0:
            print("ERROR: dry-run found no processable clips", file=sys.stderr, flush=True)
            return 2
        return 0

    if not prepared:
        summary = RunSummary(skipped_span_count=skipped)
        print_final_summary(summary, dry_run=False)
        print("ERROR: no processable clips remain; nothing was written", file=sys.stderr, flush=True)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_lock(manifest_path):
        temp_manifest = Path(str(manifest_path) + ".tmp")
        if temp_manifest.exists():
            raise CutSpansError(
                f"Refusing to proceed while a manifest temporary file exists: {temp_manifest}"
            )
        existing_manifest_rows = load_existing_manifest(manifest_path)
        reject_manifest_collisions(existing_manifest_rows, prepared)
        new_manifest_rows: list[dict[str, Any]] = []
        try:
            summary, _ = process_spans(
                prepared, outlet, dry_run=False, skipped=skipped,
                new_manifest_rows=new_manifest_rows,
            )
        finally:
            # Record clips already on disk even if process_spans raised or was interrupted.
            if new_manifest_rows:
                write_manifest_atomic(manifest_path, existing_manifest_rows, new_manifest_rows)

    print_final_summary(summary, dry_run=False)
    if summary.rms_zero_count:
        return 3
    return 0


def main() -> int:
    try:
        return run()
    except CutSpansError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
