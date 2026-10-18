"""Classify discovered YouTube clips as *founding-anchor candidates*.

This stage does not promote audio.  It verifies that a hard in-band identity
signal is literally present in the cached captions and asks a pinned mechanical
classifier whether the recording is essentially a single target speaker.  Final
acceptance still requires physical acquisition, exact boundaries, three-event
mutual voice coherence, and all-vault exclusion/comparison.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data/speech/youtube_gap_turn_candidates_v1.jsonl"
DEFAULT_CAPTIONS = Path(r"C:\melange-data\youtube_gap_turns_v1\captions")
DEFAULT_OUTPUT = ROOT / "data/speech/youtube_gap_anchor_classification_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/youtube_gap_anchor_classification_v1.receipt.json"
DEFAULT_KEY = ROOT / "fec/anthropic_key.txt"
MODEL = "claude-haiku-4-5"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for row in sorted(rows, key=lambda value: (value["member"], value["video_id"])):
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        Path(raw).unlink(missing_ok=True)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        Path(raw).unlink(missing_ok=True)


def caption_text(path: Path) -> str:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return " ".join(str(row.get("text") or "") for row in rows).strip()


def parse_response(raw: str) -> tuple[str, str, str]:
    line = next((line.strip() for line in raw.splitlines() if "|" in line), "")
    parts = [part.strip() for part in line.split("|", 2)]
    if len(parts) != 3:
        raise ValueError("classifier response is not a three-field line")
    speaker, identity, evidence = parts
    if speaker not in {"SOLO_TARGET", "MULTI_SPEAKER", "TARGET_NOT_SPEAKING"}:
        raise ValueError("invalid speaker structure")
    if identity not in {"MATCH", "MISMATCH", "NEUTRAL"}:
        raise ValueError("invalid identity verdict")
    return speaker, identity, evidence


def prompt(row: dict[str, Any], transcript: str) -> str:
    return f"""You are mechanically screening a public congressional video for a possible founding voice anchor.

TARGET: {row['member']} ({row.get('chamber') or 'Congress'})
VIDEO TITLE: {row.get('title') or ''}
CHANNEL: {row.get('channel') or ''}

Captions have no reliable speaker labels. Identity may be MATCH only from a HARD in-band signal: the target states their own name/office/district, or a different speaker explicitly recognizes or introduces the target by name/title immediately before the target speaks. Topic, title, channel, date, party, and issue position are never identity evidence. If there is no literal hard signal, identity must be NEUTRAL.

Speaker structure:
- SOLO_TARGET: the target supplies essentially all substantive speech; a brief introduction is allowed.
- MULTI_SPEAKER: an interviewer, witness, chair, or other person speaks substantively.
- TARGET_NOT_SPEAKING: the target is discussed but does not provide the substantive speech.

Return exactly one line:
<SOLO_TARGET|MULTI_SPEAKER|TARGET_NOT_SPEAKING>|<MATCH|MISMATCH|NEUTRAL>|<exact literal hard-signal phrase from captions, or NONE>

CAPTIONS:
{transcript[:9000]}"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--captions", type=Path, default=DEFAULT_CAPTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--key", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--member", action="append", default=[])
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--minimum-duration", type=float, default=40.0)
    args = parser.parse_args()

    from anthropic import Anthropic  # type: ignore

    key = args.key.read_text(encoding="utf-8").strip()
    source_rows = read_jsonl(args.input)
    wanted = set(args.member)
    source_rows = [
        row for row in source_rows
        if row.get("status") != "CAPTION_ERROR"
        and (not wanted or row["member"] in wanted)
        and (args.captions / f"{row['video_id']}.json").is_file()
    ]
    existing = {
        (row["member"], row["video_id"]): row
        for row in (read_jsonl(args.output) if args.output.exists() else [])
    }
    pending = [row for row in source_rows if (row["member"], row["video_id"]) not in existing]

    def classify(row: dict[str, Any]) -> dict[str, Any]:
        path = args.captions / f"{row['video_id']}.json"
        base = {
            "schema": "youtube-gap-anchor-classification-v1",
            "member": row["member"],
            "bioguide": row.get("bioguide"),
            "chamber": row.get("chamber"),
            "video_id": row["video_id"],
            "url": row.get("url"),
            "title": row.get("title"),
            "channel": row.get("channel"),
            "channel_id": row.get("channel_id"),
            "duration": row.get("duration"),
            "caption_sha256": None,
            "classifier_model": MODEL,
            "classified_at": utc_now(),
            "final_identity_acceptance": False,
        }
        try:
            transcript = caption_text(path)
            base["caption_sha256"] = sha256_text(transcript)
            client = Anthropic(api_key=key)
            message = client.messages.create(
                model=MODEL,
                max_tokens=180,
                temperature=0,
                messages=[{"role": "user", "content": prompt(row, transcript)}],
            )
            raw = "".join(
                block.text for block in message.content if getattr(block, "type", "") == "text"
            ).strip()
            speaker, identity, evidence = parse_response(raw)
            evidence_literal = (
                bool(normalized(evidence))
                and normalized(evidence) != "none"
                and normalized(evidence) in normalized(transcript)
            )
            candidate = (
                speaker == "SOLO_TARGET"
                and identity == "MATCH"
                and evidence_literal
                and float(row.get("duration") or 0.0) >= args.minimum_duration
            )
            return {
                **base,
                "status": "FOUNDING_ANCHOR_CANDIDATE" if candidate else "SCREENED_OUT",
                "speaker_structure": speaker,
                "identity": identity,
                "evidence": evidence,
                "evidence_literal_in_captions": evidence_literal,
                "raw_response": raw,
                "identity_basis": "hard-in-band-signal" if evidence_literal else None,
                "next_required_gate": (
                    "physical-acquisition+exact-boundary+three-event-mutual-coherence+all-vault-exclusion"
                    if candidate else None
                ),
            }
        except Exception as exc:
            return {**base, "status": "CLASSIFIER_ERROR", "error": f"{type(exc).__name__}: {exc}"[:600]}

    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(classify, row): row for row in pending}
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            existing[(row["member"], row["video_id"])] = row
            completed += 1
            if completed % 10 == 0 or completed == len(pending):
                write_jsonl_atomic(
                    args.output,
                    sorted(existing.values(), key=lambda row: (row["member"], row["video_id"])),
                )
                print(f"CLASSIFY {completed}/{len(pending)}", flush=True)
    if not pending:
        write_jsonl_atomic(
            args.output,
            sorted(existing.values(), key=lambda row: (row["member"], row["video_id"])),
        )

    rows = list(existing.values())
    receipt = {
        "schema": "youtube-gap-anchor-classification-receipt-v1",
        "status": "PASS_SCREENING_ONLY",
        "generated_at": utc_now(),
        "model": MODEL,
        "contract": {
            "title_channel_date_topic_are_identity_gates": False,
            "literal_hard_in_band_signal_required": True,
            "solo_target_required": True,
            "screening_is_final_acceptance": False,
        },
        "coverage": {
            "rows": len(rows),
            "members": len({row["member"] for row in rows}),
            "founding_anchor_candidates": sum(row["status"] == "FOUNDING_ANCHOR_CANDIDATE" for row in rows),
            "members_with_founding_anchor_candidates": len({
                row["member"] for row in rows if row["status"] == "FOUNDING_ANCHOR_CANDIDATE"
            }),
            "classifier_errors": sum(row["status"] == "CLASSIFIER_ERROR" for row in rows),
        },
        "input": str(args.input.resolve()),
        "input_sha256": sha256_file(args.input),
        "output": str(args.output.resolve()),
        "output_sha256": sha256_file(args.output),
    }
    write_text_atomic(args.receipt, json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt["coverage"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
