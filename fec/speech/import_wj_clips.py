#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Promote exactly transcript-bound Washington Journal member turns.

The two legacy manifests overlap. Version 1 contains unpadded member spans; version 2 contains
some duplicates, some additional spans, and often a 20-second host-context pad. For a duplicate
logical turn this importer keeps the longer-duration capture, value-blind, not v1 unconditionally
(CONSTRAINTS, 2026-08-25); it verifies the member and boundaries against the saved official
C-SPAN transcript, trims any v2 context pad, validates PCM/RMS, and atomically appends only
accepted rows to the canonical clip manifest.
"""

from __future__ import annotations

import argparse
import atexit
from collections import Counter
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import wave


BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "fec"))
from members.cspan_identity import CSpanIdentity  # noqa: E402


DEFAULT_ROOT = Path(r"C:\melange-data\wj_audio")
DEFAULT_CLIP_ROOT = Path(r"C:\melange-data\member_clips")
DEFAULT_TRANSCRIPTS = BASE / "cspan_out" / "transcripts"


class ImportRefusal(RuntimeError):
    pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--clip-root", type=Path, default=DEFAULT_CLIP_ROOT)
    parser.add_argument("--transcripts", type=Path, default=DEFAULT_TRANSCRIPTS)
    parser.add_argument(
        "--member",
        action="append",
        default=[],
        help="process only this exact roster member (repeatable)",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    return parser.parse_args(argv)


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ImportRefusal("invalid JSON %s:%d" % (path, number)) from exc
            if not isinstance(row, dict):
                raise ImportRefusal("non-object JSON %s:%d" % (path, number))
            rows.append(row)
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")


def pcm_rms(payload: bytes) -> float:
    if not payload:
        return 0.0
    # The original per-sample Python loop made a full 905-turn audit take tens
    # of minutes.  NumPy performs the identical PCM16 calculation in native
    # code and keeps the importer I/O-bound.
    import numpy as np

    samples = np.frombuffer(payload, dtype="<i2").astype(np.float64)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)) / 32768.0)


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    temporary = Path(str(path) + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def acquire_lock(manifest_path: Path) -> Path:
    lock = Path(str(manifest_path) + ".lock")
    try:
        descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ImportRefusal("another writer or stale manifest lock exists: %s" % lock) from exc
    os.write(descriptor, ("pid=%d\n" % os.getpid()).encode("ascii"))
    os.close(descriptor)
    atexit.register(lambda: lock.unlink(missing_ok=True))
    return lock


def logical_key(row: dict) -> tuple[str, str, float, float]:
    start = row.get("member_start", row.get("start"))
    return row.get("pid"), row.get("member"), float(start), float(row.get("end"))


def capture_duration(row: dict) -> float:
    """Length of the capture the manifest row actually points to.

    Mirrors source_path()'s own file_start fallback chain (pad_start, else start, else
    member_start) so this one helper reads a raw v1/v2 turn_manifest row and an
    already-written clips_manifest row (which carries only start/end/dur, no pad_start)
    consistently -- for the latter it reduces to exactly that row's own "dur".
    """
    member_start = float(row.get("member_start", row.get("start")))
    file_start = float(row.get("pad_start", row.get("start", member_start)))
    return float(row.get("end")) - file_start


def is_longer_than_existing(existing_duration: float | None, candidate_duration: float) -> bool:
    """True when no capture for this logical turn is manifested yet, or the candidate's
    duration exceeds the manifested one's.

    Mirrors load_candidates()'s dedupe rule (CONSTRAINTS, 2026-08-25): the longer-duration
    capture wins, value-blind. An exact duration tie keeps whichever capture already
    won -- here, the one already manifested -- so a re-run is idempotent.
    """
    return existing_duration is None or candidate_duration > existing_duration


def load_candidates(root: Path) -> tuple[list[dict], dict[str, int]]:
    v1 = read_jsonl(root / "turn_manifest.jsonl")
    v2 = read_jsonl(root / "turn_manifest_v2.jsonl")
    selected = {}
    for version, rows in (("v1", v1), ("v2", v2)):
        for row in rows:
            key = logical_key(row)
            item = dict(row)
            item["manifest_version"] = version
            # CONSTRAINTS (2026-08-25): a dual-family logical turn keeps the longer-duration
            # capture, value-blind -- not v1 unconditionally. On an exact duration tie the
            # earlier-seen version (v1) stays: a stable, arbitrary tiebreak, not a version
            # preference.
            if key in selected and capture_duration(selected[key]) >= capture_duration(item):
                continue
            selected[key] = item
    return list(selected.values()), {
        "v1_rows": len(v1),
        "v2_rows": len(v2),
        "logical_overlap": len(v1) + len(v2) - len(selected),
        "unique_candidates": len(selected),
    }


def source_path(
    root: Path, spec: dict, member_start: float | None = None
) -> tuple[Path, float, float]:
    if member_start is None:
        member_start = float(spec.get("member_start", spec.get("start")))
    file_start = float(spec.get("pad_start", spec.get("start", member_start)))
    filename = spec.get("wav") or "%s.%06d.wav" % (spec["pid"], round(file_start))
    directory = "turns" if spec["manifest_version"] == "v1" else "turns_v2"
    return root / directory / filename, member_start, file_start


def exact_transcript_binding(
    transcript_root: Path, spec: dict, identity: CSpanIdentity
) -> dict:
    path = transcript_root / (str(spec["pid"]) + ".json")
    if not path.is_file():
        raise ImportRefusal("missing_transcript")
    record = json.loads(path.read_text(encoding="utf-8-sig"))
    turns = record.get("turns") or []
    date = str(record.get("date") or "")[:10]
    title = str(record.get("title") or record.get("page_title") or "")
    start = infer_member_start(spec, turns, title, date, identity)
    end = float(spec["end"])
    expected = identity.expected_guest(title, date, [item.get("name") for item in turns])
    # Some saved C-SPAN transcripts contain duplicate-offset parser debris
    # (typically generic HOST/GUEST rows copied from another caption lane).
    # Offset uniqueness must therefore be evaluated *after* the official
    # speaker label is resolved, not before it.
    matches = []
    for index, turn in enumerate(turns):
        if turn.get("offset") is None or abs(float(turn["offset"]) - start) > 0.01:
            continue
        attributed = identity.attribute(turn.get("name"), title, date, expected=expected)
        if attributed.member == spec.get("member"):
            matches.append((index, turn))
    if len(matches) != 1:
        raise ImportRefusal("transcript_start_not_unique")
    index, turn = matches[0]
    later = [
        float(item["offset"]) for item in turns[index + 1:]
        if item.get("offset") is not None and float(item["offset"]) > start
    ]
    if not later:
        raise ImportRefusal("transcript_end_not_next_turn")
    next_turn = min(later)
    if spec.get("manifest_version") == "v2" and spec.get("src") == "gap":
        if abs(next_turn - end) > 0.01:
            raise ImportRefusal("transcript_end_not_next_turn")
    elif end > next_turn + 0.01:
        # V1 also contains intentionally short, in-turn 8-second excerpts.  A
        # cut may end before the next labelled turn but must never cross it.
        raise ImportRefusal("transcript_interval_crosses_next_turn")
    attributed = identity.attribute(turn.get("name"), title, date, expected=expected)
    if attributed.member != spec.get("member"):
        raise ImportRefusal("identity_mismatch")
    text = " ".join(str(turn.get("text") or "").split())
    if len(text.split()) < 1:
        raise ImportRefusal("empty_transcript_turn")
    return {
        "member_start": start,
        "date": date,
        "title": title,
        "url": record.get("url") or "",
        "speaker_label": turn.get("name") or "",
        "text": text,
        "transcript_path": str(path.resolve()),
        "ordinal": index + 1,
    }


def infer_member_start(
    spec: dict,
    turns: list[dict],
    title: str,
    date: str,
    identity: CSpanIdentity,
) -> float:
    """Resolve the true speech start for old padded v2 ``src=gap`` rows.

    The gap fetcher saved a clip beginning up to 20 seconds before the member turn but did not
    persist ``member_start``.  The start may be recovered only when the saved official transcript
    contains exactly one turn in that pad interval which independently resolves to the manifest
    member.  Ambiguity continues to fail closed.
    """
    if spec.get("member_start") is not None:
        return float(spec["member_start"])
    file_start = float(spec.get("start"))
    if spec.get("manifest_version") != "v2" or spec.get("src") != "gap":
        return file_start
    end = float(spec["end"])
    expected = identity.expected_guest(title, date, [turn.get("name") for turn in turns])
    labelled = []
    for index, turn in enumerate(turns):
        if turn.get("offset") is None:
            continue
        offset = float(turn["offset"])
        attributed = identity.attribute(turn.get("name"), title, date, expected=expected)
        if attributed.member == spec.get("member"):
            labelled.append((index, offset))

    # The gap file's end was generated from the next official transcript turn
    # and is a stronger boundary than the historically assumed 20-second pad.
    # Recover the labelled member turn whose next turn is exactly that end.
    boundary_candidates = []
    for index, offset in labelled:
        if offset < file_start - 0.01 or offset >= end - 0.01:
            continue
        later = [
            float(item["offset"])
            for item in turns[index + 1 :]
            if item.get("offset") is not None and float(item["offset"]) > offset
        ]
        if later and abs(min(later) - end) <= 0.01:
            boundary_candidates.append(offset)
    if len(boundary_candidates) == 1:
        return boundary_candidates[0]

    candidates = [
        offset
        for _, offset in labelled
        if file_start - 0.01 <= offset <= min(end, file_start + 20.01)
    ]
    if len(candidates) != 1:
        raise ImportRefusal("padded_member_start_not_unique")
    return candidates[0]


def read_member_payload(path: Path, member_start: float, file_start: float, end: float):
    if not path.is_file():
        raise ImportRefusal("missing_wav")
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        rate = source.getframerate()
        width = source.getsampwidth()
        total_frames = source.getnframes()
        if (channels, rate, width) != (1, 16000, 2):
            raise ImportRefusal("source_wav_contract")
        measured = total_frames / float(rate)
        expected = end - file_start
        # Several legacy downloads end a few seconds before the advertised
        # transcript boundary.  A short physical file is still a safe subset
        # of the labelled member turn; a long file could cross into the next
        # speaker and remains a refusal.
        if measured - expected > 0.75:
            raise ImportRefusal("source_duration_mismatch")
        trim_frames = round((member_start - file_start) * rate)
        requested_frames = round((end - member_start) * rate)
        available_frames = total_frames - trim_frames
        wanted_frames = min(requested_frames, available_frames)
        if trim_frames < 0 or wanted_frames <= 0 or trim_frames > total_frames:
            raise ImportRefusal("invalid_trim_bounds")
        source.setpos(min(trim_frames, total_frames))
        payload = source.readframes(wanted_frames)
    if abs(len(payload) // 2 - wanted_frames) > rate:
        raise ImportRefusal("short_member_payload")
    if len(payload) // 2 < rate * 4:
        raise ImportRefusal("member_payload_under_four_seconds")
    rms = pcm_rms(payload)
    if rms <= 0.0:
        raise ImportRefusal("zero_rms")
    physical_end = member_start + (len(payload) // 2) / float(rate)
    return rate, payload, rms, physical_end


def make_row(
    spec: dict, binding: dict, src: Path, out: Path, rate: int, payload: bytes, rms: float,
    physical_end: float,
) -> dict:
    start = float(binding["member_start"])
    end = float(physical_end)
    text_digest = sha256_text(binding["text"])
    artifact_id = sha256_text("cspan\0%s\0%s" % (spec["pid"], binding["url"]))
    turn_id = sha256_text(
        "%s\0%s\0%s\0%s" % (artifact_id, binding["ordinal"], spec["member"], text_digest)
    )
    return {
        "member": spec["member"],
        "member_slug": slug(spec["member"]),
        "outlet": "CSPAN",
        "show": "Washington Journal",
        "sid": str(spec["pid"]),
        "seg": round(start),
        "start": start,
        "end": end,
        "dur": round(end - start, 3),
        "transcript_turn_end": float(spec["end"]),
        "physical_end_shortfall_sec": round(max(0.0, float(spec["end"]) - end), 6),
        "match": 1.0,
        "alignment_method": "official-cspan-speaker+official-offset-boundaries",
        "speaker_label": binding["speaker_label"],
        "artifact_id": artifact_id,
        "turn_id": turn_id,
        "original_ordinal": binding["ordinal"],
        "words": len(binding["text"].split()),
        "date": binding["date"],
        "package": binding["url"],
        "transcript_path": binding["transcript_path"],
        "text_sha256": text_digest,
        "src_wav": str(src.resolve()),
        "source_audio_sha256": sha256(src),
        "out_wav": str(out.resolve()),
        "bytes": out.stat().st_size,
        "samplerate": rate,
        "frames": len(payload) // 2,
        "rms": rms,
        "sha256": sha256(out),
        "legacy_manifest_version": spec["manifest_version"],
    }


def write_trimmed(path: Path, rate: int, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".part" + path.suffix)
    with wave.open(str(temporary), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(payload)
    os.replace(temporary, path)


def source_is_exact_payload(path: Path, rate: int, payload: bytes) -> bool:
    """Return true only when the source WAV is exactly the accepted member payload.

    `read_member_payload` deliberately tolerates a source ending up to 0.75 seconds
    after the official transcript boundary so that it can make a safe, exact cut.
    Such a source must never be reused as the canonical output: doing so would bind
    the shorter manifest frame count to a physical WAV that still contains trailing
    audio.
    """
    with wave.open(str(path), "rb") as source:
        return (
            source.getnchannels() == 1
            and source.getframerate() == rate
            and source.getsampwidth() == 2
            and source.getnframes() == len(payload) // 2
        )


def main(argv=None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    clip_root = args.clip_root.resolve()
    manifest_path = clip_root / "clips_manifest.jsonl"
    rejection_path = clip_root / "clips_rejections.jsonl"
    preexisting_rows = read_jsonl(manifest_path) if manifest_path.exists() else []
    preexisting_duration_by_logical = {}
    for row in preexisting_rows:
        key = (row.get("outlet"), str(row.get("sid")), int(row.get("seg")), row.get("member"))
        duration = capture_duration(row)
        if duration > preexisting_duration_by_logical.get(key, -1.0):
            preexisting_duration_by_logical[key] = duration
    candidates, census = load_candidates(root)
    if args.member:
        wanted = set(args.member)
        candidates = [spec for spec in candidates if spec.get("member") in wanted]
        census["member_filter"] = sorted(wanted)
        census["filtered_candidates"] = len(candidates)
    identity = CSpanIdentity()
    accepted_specs = []
    accepted_existing = 0
    accepted_existing_members = set()
    shorter_existing_twins = 0
    refusals = []
    reason_counts = Counter()

    for spec in candidates:
        try:
            binding = exact_transcript_binding(args.transcripts.resolve(), spec, identity)
            logical = (
                "CSPAN", str(spec["pid"]), round(float(binding["member_start"])), spec["member"]
            )
            existing_duration = preexisting_duration_by_logical.get(logical)
            candidate_duration = float(spec["end"]) - float(binding["member_start"])
            if not is_longer_than_existing(existing_duration, candidate_duration):
                accepted_existing += 1
                accepted_existing_members.add(spec["member"])
                continue
            # CONSTRAINTS (2026-08-25): the already-manifested capture for this logical
            # turn is shorter than this candidate -- keep the longer capture, value-blind.
            # Do not skip; fall through so the candidate is fully verified and, on
            # success, imported alongside the existing shorter row at write time (never
            # deleting or rewriting it -- see the write-time gate below).
            src, member_start, file_start = source_path(
                root, spec, float(binding["member_start"])
            )
            rate, payload, rms, physical_end = read_member_payload(
                src, member_start, file_start, float(spec["end"])
            )
            if existing_duration is not None:
                # The write gate compares physical against physical; do the same here so
                # dry-run does not count a twin that is longer only by transcript end.
                if not is_longer_than_existing(existing_duration, physical_end - member_start):
                    accepted_existing += 1
                    accepted_existing_members.add(spec["member"])
                    continue
                shorter_existing_twins += 1
            accepted_specs.append((
                spec, binding, src, member_start, file_start, rate, payload, rms, physical_end
            ))
        except (ImportRefusal, OSError, ValueError, KeyError, wave.Error) as exc:
            reason = str(exc) or type(exc).__name__
            reason_counts[reason] += 1
            refusals.append({
                "outlet": "CSPAN",
                "sid": spec.get("pid"),
                "seg": spec.get("member_start", spec.get("start")),
                "member": spec.get("member"),
                "reason": reason,
                "manifest_version": spec.get("manifest_version"),
                "attempted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            })

    print(json.dumps({
        **census,
        "accepted_candidates": len(accepted_specs) + accepted_existing,
        "accepted_existing_skipped_before_pcm": accepted_existing,
        "shorter_existing_twins_retained": shorter_existing_twins,
        "refused_candidates": len(refusals),
        "refusal_reasons": dict(sorted(reason_counts.items())),
        "accepted_members": len(
            {item[0]["member"] for item in accepted_specs}
            | accepted_existing_members
        ),
        "accepted_by_manifest": dict(sorted(Counter(
            item[0]["manifest_version"] for item in accepted_specs
        ).items())),
        "accepted_requiring_context_trim": sum(
            item[3] != item[4] for item in accepted_specs
        ),
        "mode": "write" if args.write else "dry-run",
    }, indent=2), flush=True)
    if args.dry_run:
        return 0

    lock = acquire_lock(manifest_path)
    existing_rows = read_jsonl(manifest_path)
    existing_duration_by_logical = {}
    for row in existing_rows:
        key = (row.get("outlet"), str(row.get("sid")), int(row.get("seg")), row.get("member"))
        duration = capture_duration(row)
        if duration > existing_duration_by_logical.get(key, -1.0):
            existing_duration_by_logical[key] = duration
    existing_by_output = {
        os.path.normcase(str(Path(row["out_wav"]).resolve())): index
        for index, row in enumerate(existing_rows)
        if row.get("out_wav")
    }
    new_rows = []
    repaired_rows = 0
    shorter_twins_added = 0
    for spec, binding, src, member_start, file_start, rate, payload, rms, physical_end in accepted_specs:
        logical = ("CSPAN", str(spec["pid"]), round(member_start), spec["member"])
        candidate_duration = float(physical_end) - float(member_start)
        existing_duration = existing_duration_by_logical.get(logical)
        # CONSTRAINTS (2026-08-25): keep the longer-duration capture, value-blind. An
        # already-manifested SHORTER twin must not block a longer candidate: skip only
        # when the existing row's duration already covers or exceeds this candidate's.
        if existing_duration is not None and not is_longer_than_existing(
            existing_duration, candidate_duration
        ):
            continue
        supersedes_shorter_twin = existing_duration is not None
        if member_start == file_start and source_is_exact_payload(src, rate, payload):
            out = src
        else:
            name = "%s.%06d.wav" % (spec["pid"], round(member_start))
            if supersedes_shorter_twin:
                # The canonical name for this logical turn is already claimed by the
                # shorter existing row. Disambiguate by end-second so the longer capture
                # is imported as an ADDITIONAL row -- never deleting or rewriting the
                # existing (shorter) row or its WAV.
                name = "%s.%06d.%06d.wav" % (
                    spec["pid"], round(member_start), round(physical_end)
                )
            out = clip_root / "wj" / slug(spec["member"]) / name
            output_key = os.path.normcase(str(out.resolve()))
            if out.exists() and output_key not in existing_by_output:
                raise ImportRefusal("refusing to overwrite unmanifested output: %s" % out)
            # A stale managed output must be rewritten, not merely re-described: make_row()
            # takes "frames" from the freshly computed payload but "bytes"/"sha256" from the
            # file on disk, so reusing an output whose frame count no longer matches emits a
            # self-inconsistent manifest row that build_descriptive_speech.wav_contract()
            # later rejects ("accepted clip WAV/frame contract failed").
            if not out.exists() or not source_is_exact_payload(out, rate, payload):
                write_trimmed(out, rate, payload)
        row = make_row(spec, binding, src, out, rate, payload, rms, physical_end)
        output_key = os.path.normcase(str(out.resolve()))
        if output_key in existing_by_output:
            index = existing_by_output[output_key]
            if index < len(existing_rows):
                existing_rows[index] = row
            else:
                # Same output written earlier in this run: replace that new row.
                new_rows[index - len(existing_rows)] = row
            repaired_rows += 1
            existing_duration_by_logical[logical] = max(
                existing_duration_by_logical.get(logical, -1.0), candidate_duration
            )
            continue
        new_rows.append(row)
        existing_by_output[output_key] = len(existing_rows) + len(new_rows) - 1
        if supersedes_shorter_twin:
            shorter_twins_added += 1
        existing_duration_by_logical[logical] = max(
            existing_duration_by_logical.get(logical, -1.0), candidate_duration
        )

    write_jsonl_atomic(manifest_path, existing_rows + new_rows)
    existing_rejections = read_jsonl(rejection_path) if rejection_path.exists() else []
    known_rejections = {
        (row.get("outlet"), str(row.get("sid")), str(row.get("seg")), row.get("member"), row.get("reason"))
        for row in existing_rejections
    }
    new_rejections = [
        row for row in refusals
        if (row.get("outlet"), str(row.get("sid")), str(row.get("seg")), row.get("member"), row.get("reason"))
        not in known_rejections
    ]
    write_jsonl_atomic(rejection_path, existing_rejections + new_rejections)
    lock.unlink(missing_ok=True)
    print("WJ IMPORT wrote=%d repaired=%d existing=%d rejected=%d manifest=%s" % (
        len(new_rows), repaired_rows,
        accepted_existing + len(accepted_specs) - len(new_rows) - repaired_rows,
        len(new_rejections), manifest_path
    ), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
